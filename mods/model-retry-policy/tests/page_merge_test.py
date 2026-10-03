"""The page's ``mergePatch`` — the document ``save()`` hands to ``config.set`` — run under Node.

``ui/page.mjs`` touches the DOM only inside ``mount``, so Node can import it and
call the exported helper directly. ``config.set`` merges top-level keys only, so
the page builds each touched key whole; the regression here is the ``enabled``
flag: a boolean spread through the object merge became ``{}``, which the hook
read as "off", and every mechanism the user clicked rendered back as "host".
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def merge(current, patch):
    script = (
        f"import {{ mergePatch }} from {json.dumps((ROOT / 'ui' / 'page.mjs').as_uri())};\n"
        f"process.stdout.write(JSON.stringify(mergePatch({json.dumps(current)}, {json.dumps(patch)})));"
    )
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_scalar_keys_pass_through_as_themselves():
    assert merge({}, {"enabled": True}) == {"enabled": True}
    assert merge({"enabled": True}, {"enabled": False}) == {"enabled": False}
    # the corrupted value already on disk is simply overwritten by the real boolean
    assert merge({"enabled": {}}, {"enabled": True}) == {"enabled": True}


def test_object_keys_are_deep_merged_against_the_current_document():
    current = {"retries": {"mode": "count", "count": 7}, "delay": {"mode": "fixed", "interval_s": 30}}
    out = merge(current, {"retries": {"mode": "infinite"}, "delay": {"mode": "exponential"}, "fallback": {"enabled": False}})
    assert out == {
        "retries": {"mode": "infinite", "count": 7},
        "delay": {"mode": "exponential", "interval_s": 30},
        "fallback": {"enabled": False},
    }


def test_null_inside_profiles_deletes_that_profile_and_a_top_level_null_is_kept():
    current = {"fallback": {"enabled": True, "profile": "a", "profiles": {"a": {"name": "A", "steps": []}, "b": {"name": "B", "steps": []}}}}
    out = merge(current, {"fallback": {"profiles": {"a": None}, "enabled": False, "profile": ""}})
    assert out == {"fallback": {"enabled": False, "profile": "", "profiles": {"b": {"name": "B", "steps": []}}}}
    assert merge(current, {"fallback": None}) == {"fallback": None}


def test_arrays_replace_rather_than_merge():
    current = {"fallback": {"profiles": {"a": {"steps": [{"model": "x", "retries": 2}, {"model": "y", "retries": 1}]}}}}
    out = merge(current, {"fallback": {"profiles": {"a": {"steps": [{"model": "y", "retries": 1}]}}}})
    assert out["fallback"]["profiles"]["a"]["steps"] == [{"model": "y", "retries": 1}]


def test_a_scalar_where_an_object_used_to_be_is_replaced_not_spread():
    assert merge({"retries": 3}, {"retries": {"mode": "count"}}) == {"retries": {"mode": "count"}}
