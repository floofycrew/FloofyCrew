"""Schema tests (Requirement 1.1–1.7, 1.9, 1.10; task 2.4).

Every example mod validates against ``floofy.schema.json`` (and the patch example
against ``patch.schema.json``); targeted mutations fail at the right instance path.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.schema import SCHEMA_IDS, load_schema, schema_path
from floofy_core.schema.check import SchemaError, validate_instance

from floofy_testing import EXAMPLES_DIR, KINDS, REPO_ROOT, read_manifest

FLOOFY = load_schema("floofy")
PATCH = load_schema("patch")


def _paths(errors: list[SchemaError]) -> list[str]:
    return [e.path for e in errors]


# --- the shipped schemas and examples ---------------------------------------------


def test_schema_ids_and_loader() -> None:
    assert FLOOFY["$id"] == SCHEMA_IDS["floofy"] == "https://floofycrew.dev/schema/floofy-1.json"
    assert PATCH["$id"] == SCHEMA_IDS["patch"] == "https://floofycrew.dev/schema/patch-1.json"
    assert FLOOFY["$schema"] == PATCH["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert load_schema("floofy") is FLOOFY  # cached
    assert schema_path("floofy.schema.json") == schema_path("floofy")
    with pytest.raises(KeyError):
        load_schema("nope")


def test_every_kind_has_an_example() -> None:
    kinds = {read_manifest(p.parent)["parts"][0]["kind"] for p in EXAMPLES_DIR.glob("*/floofy.json")}
    assert kinds == set(KINDS)
    assert len(KINDS) == 10


@pytest.mark.parametrize("kind", KINDS)
def test_example_validates(kind: str) -> None:
    manifest = read_manifest(EXAMPLES_DIR / kind)
    assert validate_instance(manifest, FLOOFY) == []
    assert manifest["schema"] == 1 and "floofycrew" in manifest["dependsOn"]


def test_patch_example_descriptor_validates() -> None:
    root = EXAMPLES_DIR / "patch"
    part = read_manifest(root)["parts"][0]
    descriptor = json.loads((root / part["path"]).read_text(encoding="utf-8"))
    assert validate_instance(descriptor, PATCH) == []


def test_example_hashes_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "hash_example_files.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- mutations fail at the right path ---------------------------------------------


@pytest.fixture
def theme_manifest() -> dict:
    return copy.deepcopy(read_manifest(EXAMPLES_DIR / "theme"))


def test_missing_framework_dependency(theme_manifest: dict) -> None:
    del theme_manifest["dependsOn"]["floofycrew"]
    errors = validate_instance(theme_manifest, FLOOFY)
    assert [(e.path, e.keyword) for e in errors] == [("/dependsOn", "required")]
    assert "floofycrew" in errors[0].details


@pytest.mark.parametrize("bad_id", ["Bad", "1abc", "a", "a" * 65, "with space", "under_score-ok!"])
def test_bad_id(theme_manifest: dict, bad_id: str) -> None:
    theme_manifest["id"] = bad_id
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/id"]


@pytest.mark.parametrize("bad_sha", ["xyz", "0" * 63, "0" * 65, "G" * 64, "A" * 64])
def test_bad_sha256(theme_manifest: dict, bad_sha: str) -> None:
    theme_manifest["files"][0]["sha256"] = bad_sha
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/files/0/sha256"]


@pytest.mark.parametrize("bad_path", ["/etc/theme.json", "../theme.json", "a/../theme.json", "a/..", "..", "a\\theme.json", "a\ntheme.json"])
def test_part_path_escapes(theme_manifest: dict, bad_path: str) -> None:
    theme_manifest["parts"][0]["path"] = bad_path
    assert set(_paths(validate_instance(theme_manifest, FLOOFY))) == {"/parts/0/path"}


def test_theme_part_must_point_at_theme_json(theme_manifest: dict) -> None:
    theme_manifest["parts"][0]["path"] = "theme/"
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/parts/0/path"]


def test_unknown_kind(theme_manifest: dict) -> None:
    theme_manifest["parts"][0]["kind"] = "widget"
    errors = validate_instance(theme_manifest, FLOOFY)
    assert _paths(errors) == ["/parts/0/kind"]
    assert "widget" in errors[0].message and "theme" in errors[0].message


def test_unknown_part_field_and_top_level_field(theme_manifest: dict) -> None:
    theme_manifest["parts"][0]["bogus"] = 1
    theme_manifest["bogus"] = 1
    theme_manifest["x-extension"] = {"anything": True}  # x- keys are allowed
    assert sorted(_paths(validate_instance(theme_manifest, FLOOFY))) == ["/bogus", "/parts/0/bogus"]


@pytest.mark.parametrize("value", [2, 0, True, "1", 1.5])
def test_schema_number_must_be_one(theme_manifest: dict, value) -> None:
    theme_manifest["schema"] = value
    assert set(_paths(validate_instance(theme_manifest, FLOOFY))) == {"/schema"}


def test_kirocrew_compat_object(theme_manifest: dict) -> None:
    theme_manifest["kirocrew"] = {"editions": ["internal"]}
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/kirocrew"]  # version required
    theme_manifest["kirocrew"] = {"version": "*", "editions": ["internal", "internal"], "strict": "yes"}
    assert sorted(_paths(validate_instance(theme_manifest, FLOOFY))) == ["/kirocrew/editions/1", "/kirocrew/strict"]
    theme_manifest["kirocrew"] = {"version": "*", "editions": ["community"]}
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/kirocrew/editions/0"]


def test_dependency_maps_accept_both_forms(theme_manifest: dict) -> None:
    for name in ("dependsOn", "recommends", "suggests", "conflicts", "breaks"):
        theme_manifest.setdefault(name, {})["other-mod"] = {"range": "^1.2", "reason": "because"}
        theme_manifest[name]["third-mod"] = ">=1 <3"
    assert validate_instance(theme_manifest, FLOOFY) == []
    theme_manifest["conflicts"]["Bad Id"] = "*"
    theme_manifest["breaks"]["fourth-mod"] = 3
    assert sorted(_paths(validate_instance(theme_manifest, FLOOFY))) == ["/breaks/fourth-mod", "/conflicts/Bad Id"]


def test_network_block(theme_manifest: dict) -> None:
    theme_manifest["network"] = {"hosts": ["api.example.com", "*.example.com", "api.example.com:8443"], "credentials": True}
    assert validate_instance(theme_manifest, FLOOFY) == []
    theme_manifest["network"] = {"hosts": ["Bad Host", "http://x.example.com"]}
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/network/hosts/0", "/network/hosts/1"]


def test_parts_and_files_need_at_least_one(theme_manifest: dict) -> None:
    theme_manifest["parts"] = []
    theme_manifest["files"] = []
    assert sorted(_paths(validate_instance(theme_manifest, FLOOFY))) == ["/files", "/parts"]


def test_config_part_needs_values_or_path(theme_manifest: dict) -> None:
    theme_manifest["parts"] = [{"kind": "config", "side": "gateway"}]
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/parts/0"]
    theme_manifest["parts"] = [{"kind": "config", "side": "gateway", "values": {"dashboard.compact": True}}]
    assert validate_instance(theme_manifest, FLOOFY) == []


def test_python_hook_and_spa_fields(theme_manifest: dict) -> None:
    theme_manifest["parts"] = [{"kind": "python-hook", "side": "gateway", "path": "hook/"}]
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/parts/0"]  # module required
    theme_manifest["parts"] = [{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "1bad"}]
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/parts/0/module"]
    theme_manifest["parts"] = [{"kind": "spa", "side": "spa", "path": "spa/main.js", "activation": "lazy"}]
    assert _paths(validate_instance(theme_manifest, FLOOFY)) == ["/parts/0/activation"]


def test_patch_descriptor_shapes() -> None:
    good = {"target": "kiro_crew/static/dist/index.html", "ops": [{"op": "append-head", "content": "<style></style>"}]}
    assert validate_instance(good, PATCH) == []
    assert _paths(validate_instance({"target": "/abs.html", "ops": []}, PATCH)) == ["/target", "/ops"]
    missing_fp = {"target": "x.html", "ops": [{"op": "replace", "content": "y"}]}
    assert _paths(validate_instance(missing_fp, PATCH)) == ["/ops/0"]
    bad_op = {"target": "x.html", "ops": [{"op": "delete", "fingerprint": "a", "content": "b"}]}
    assert _paths(validate_instance(bad_op, PATCH)) == ["/ops/0/op"]
    permissive = {**good, "importMap": {"/assets/chunk-abc.js": "/apps/floofycrew/ui/patched/chunk.js"}, "fromBuild": "0.7.0.5", "toBuild": "0.8.0"}
    assert validate_instance(permissive, PATCH) == []


# --- properties -----------------------------------------------------------------------

VALID_ID = st.from_regex(r"^[a-z][a-z0-9_-]{1,63}$", fullmatch=True)
SEMVER = st.from_regex(
    r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})(-(0|[1-9][0-9]{0,2}|[0-9]*[a-zA-Z-][0-9a-zA-Z-]{0,5})(\.(0|[1-9][0-9]{0,2}|[0-9]*[a-zA-Z-][0-9a-zA-Z-]{0,5})){0,2})?(\+[0-9a-zA-Z-]{1,6}(\.[0-9a-zA-Z-]{1,6}){0,2})?$",
    fullmatch=True,
)


@settings(max_examples=150, deadline=None)
@given(VALID_ID, SEMVER)
def test_valid_id_and_version_pass(mod_id: str, version: str) -> None:
    """**Validates: Requirements 1.2** — any id matching the documented pattern and any
    SemVer 2.0 string are accepted by the schema."""
    manifest = copy.deepcopy(read_manifest(EXAMPLES_DIR / "theme"))
    manifest["id"] = mod_id
    manifest["version"] = version
    assert validate_instance(manifest, FLOOFY) == []


_ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,63}")


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=1, max_size=70).filter(lambda s: not _ID_RE.fullmatch(s)))
def test_invalid_ids_fail_at_id(mod_id: str) -> None:
    """**Validates: Requirements 1.2** — anything outside the id pattern is rejected at /id
    (including a trailing newline, which JSON Schema's ``$`` does not forgive)."""
    manifest = copy.deepcopy(read_manifest(EXAMPLES_DIR / "theme"))
    manifest["id"] = mod_id
    assert "/id" in _paths(validate_instance(manifest, FLOOFY))
