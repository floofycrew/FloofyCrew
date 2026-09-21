"""Patch descriptor tests (Requirement 5.5; task 3.3).

Exactly-once fingerprints (literal and regex), marker idempotency (explicit and
implicit), ``appliesTo``/``fromBuild``/``toBuild`` gating, every op, per-op
atomicity (one skipped op never blocks or partially rewrites the others), SKIP
diagnostics naming the file and op, the fingerprint reporter, and the import-map
helpers from spike 1.4 — against a synthetic shell, the example patch mod and the
real 0.7.0.5 ``index.html`` copy when present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.patches import (
    PatchDescriptor,
    PatchDescriptorError,
    PatchOp,
    apply_descriptor,
    drop_modulepreload,
    find_import_map,
    fingerprint_report,
    merge_import_map,
)

from floofy_testing import EXAMPLES_DIR, FAKE_INDEX_HTML, REPO_ROOT

REAL_INDEX = REPO_ROOT / ".scratch" / "dist-0.7.0.5" / "index.html"
EXAMPLE_DESCRIPTOR = EXAMPLES_DIR / "patch" / "patches" / "index-boot.json"


def _descriptor(ops: list[dict], **extra) -> PatchDescriptor:
    document = {"schema": 1, "target": "kiro_crew/static/dist/index.html", "ops": ops, **extra}
    return PatchDescriptor.from_dict(document, source="test")


# --- loading -------------------------------------------------------------------------


def test_load_example_descriptor() -> None:
    descriptor = PatchDescriptor.load(EXAMPLE_DESCRIPTOR)
    assert descriptor.target == "kiro_crew/static/dist/index.html"
    assert descriptor.applies_to is not None and descriptor.applies_to.contains("0.7.0")
    assert len(descriptor.ops) == 1 and descriptor.ops[0].marker == 'id="floofy-example-boot"'
    assert descriptor.markers == ['id="floofy-example-boot"']


def test_load_rejects_schema_violations_and_bad_fields(tmp_path: Path) -> None:
    with pytest.raises(PatchDescriptorError) as info:
        _descriptor([{"op": "replace", "content": "x"}])  # missing fingerprint
    assert "/ops/0" in str(info.value)
    with pytest.raises(PatchDescriptorError) as info:
        _descriptor([{"op": "replace", "fingerprint": "(", "regex": True, "content": "x"}])
    assert "regular expression" in str(info.value)
    with pytest.raises(PatchDescriptorError):
        _descriptor([{"op": "append-head", "content": "x"}], appliesTo=">=a.b")
    with pytest.raises(PatchDescriptorError):
        _descriptor([{"op": "append-head", "content": "x"}], fromBuild="0.7.0.x")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(PatchDescriptorError) as info:
        PatchDescriptor.load(bad)
    assert info.value.source == str(bad)
    with pytest.raises(PatchDescriptorError):
        PatchDescriptor.from_dict({"target": "kiro_crew/x.py", "ops": []})


# --- gating --------------------------------------------------------------------------


def test_applies_to_gates_on_base_version() -> None:
    descriptor = _descriptor([{"op": "append-head", "content": "<x/>"}], appliesTo=">=0.7.0 <0.9.0")
    assert descriptor.applicability("0.7.0.5") is None
    assert descriptor.applicability("0.8.0rc1") is None  # rc base 0.8.0 is inside
    assert "outside appliesTo" in (descriptor.applicability("0.9.0") or "")
    assert descriptor.applicability(None) is None
    result = apply_descriptor("<head></head>", descriptor, "0.6.0")
    assert result.codes() == ["NotApplicable"] and not result.changed and result.text == "<head></head>"
    assert result.skipped[0].op_index is None and "SKIP kiro_crew/static/dist/index.html: NotApplicable" in result.diagnostics()[0]


def test_from_and_to_build_are_inclusive_exclusive() -> None:
    descriptor = _descriptor([{"op": "append-head", "content": "<x/>"}], fromBuild="0.7.0.4", toBuild="0.7.0.6")
    assert descriptor.applicability("0.7.0.3") is not None
    assert descriptor.applicability("0.7.0.4") is None
    assert descriptor.applicability("0.7.0.5") is None
    assert descriptor.applicability("0.7.0.6") is not None


# --- ops -----------------------------------------------------------------------------


def test_every_op_on_a_synthetic_shell() -> None:
    descriptor = _descriptor(
        [
            {"op": "insert-before", "fingerprint": '<script type="module"', "content": "<!--before-->", "marker": "<!--before-->"},
            {"op": "insert-after", "fingerprint": "<title>KiroCrew</title>", "content": "<!--after-->"},
            {"op": "replace", "fingerprint": r'lang="en"', "content": 'lang="en-GB"', "marker": 'lang="en-GB"'},
            {"op": "append-head", "content": "<style id=\"floofy-css\">:root{}</style>", "marker": 'id="floofy-css"'},
            {"op": "replace", "fingerprint": r'data-theme="(dark|light)"', "regex": True, "content": 'data-theme="floofy"'},
        ]
    )
    result = apply_descriptor(FAKE_INDEX_HTML, descriptor, "0.7.0.5")
    assert result.skipped == [] and [a.op_index for a in result.applied] == [0, 1, 2, 3, 4]
    text = result.text
    assert '<!--before--><script type="module"' in text
    assert "<title>KiroCrew</title><!--after-->" in text
    assert 'lang="en-GB"' in text and 'lang="en"' not in text.replace('lang="en-GB"', "")
    assert text.count("</head>") == 1 and '<style id="floofy-css">:root{}</style></head>' in text
    assert 'data-theme="floofy"' in text and 'data-theme="dark"' not in text
    # positions are ascending for these anchors and reflect the text as left by earlier ops
    assert all(a.position >= 0 for a in result.applied)


def test_second_apply_is_all_already_applied() -> None:
    descriptor = _descriptor(
        [
            {"op": "insert-before", "fingerprint": '<script type="module"', "content": "<!--before-->", "marker": "<!--before-->"},
            {"op": "insert-after", "fingerprint": "<title>KiroCrew</title>", "content": "<!--after-->"},  # implicit marker
        ]
    )
    once = apply_descriptor(FAKE_INDEX_HTML, descriptor, "0.7.0.5")
    twice = apply_descriptor(once.text, descriptor, "0.7.0.5")
    assert twice.codes() == ["AlreadyApplied", "AlreadyApplied"] and not twice.changed and twice.text == once.text


def test_skips_are_per_op_and_never_partial() -> None:
    descriptor = _descriptor(
        [
            {"op": "replace", "fingerprint": "nowhere-to-be-found", "content": "X"},
            {"op": "insert-after", "fingerprint": "<title>KiroCrew</title>", "content": "<!--ok-->"},
            {"op": "replace", "fingerprint": "/assets/", "content": "Y"},  # appears twice: ambiguous
        ]
    )
    result = apply_descriptor(FAKE_INDEX_HTML, descriptor, "0.7.0.5")
    assert [a.op_index for a in result.applied] == [1]
    assert [(s.op_index, s.code) for s in result.skipped] == [(0, "FingerprintMiss"), (2, "FingerprintAmbiguous")]
    assert "X" not in result.text.replace("<!--ok-->", "") or "nowhere" not in result.text
    assert result.text.count("/assets/") == 2 and "Y" not in result.text
    miss, ambiguous = result.diagnostics()
    assert miss.startswith("SKIP kiro_crew/static/dist/index.html#ops/0: FingerprintMiss") and "index.html" in miss
    assert "matches 2 times" in ambiguous and "#ops/2" in ambiguous


def test_regex_fingerprint_exactly_once() -> None:
    text = "aaa bbb aaa"
    one = _descriptor([{"op": "replace", "fingerprint": r"b+", "regex": True, "content": "B"}])
    assert apply_descriptor(text, one).text == "aaa B aaa"
    many = _descriptor([{"op": "replace", "fingerprint": r"a+", "regex": True, "content": "A"}])
    assert apply_descriptor(text, many).codes() == ["FingerprintAmbiguous"]
    none = _descriptor([{"op": "insert-before", "fingerprint": r"z+", "regex": True, "content": "Z"}])
    assert apply_descriptor(text, none).codes() == ["FingerprintMiss"]


def test_append_head_needs_exactly_one_head_close() -> None:
    descriptor = _descriptor([{"op": "append-head", "content": "<x/>"}])
    assert apply_descriptor("<html><body/></html>", descriptor).codes() == ["FingerprintMiss"]
    assert apply_descriptor("</head></head>", descriptor).codes() == ["FingerprintAmbiguous"]
    assert apply_descriptor("<head></head>", descriptor).text == "<head><x/></head>"


def test_later_op_can_anchor_on_earlier_content() -> None:
    descriptor = _descriptor(
        [
            {"op": "append-head", "content": "<meta name=\"floofy\">", "marker": 'name="floofy"'},
            {"op": "insert-after", "fingerprint": '<meta name="floofy">', "content": "<!--chained-->"},
        ]
    )
    result = apply_descriptor("<head></head>", descriptor)
    assert result.text == '<head><meta name="floofy"><!--chained--></head>'


def test_example_descriptor_on_fake_and_real_shell() -> None:
    descriptor = PatchDescriptor.load(EXAMPLE_DESCRIPTOR)
    result = apply_descriptor(FAKE_INDEX_HTML, descriptor, "0.7.0.5")
    assert result.applied and 'id="floofy-example-boot"' in result.text
    if REAL_INDEX.is_file():
        real = REAL_INDEX.read_text(encoding="utf-8")
        result = apply_descriptor(real, descriptor, "0.7.0.5")
        assert [a.op for a in result.applied] == ["insert-before"] and result.skipped == []
        assert result.text.index('id="floofy-example-boot"') < result.text.index('<script type="module" crossorigin src="/assets/main-')
        assert apply_descriptor(result.text, descriptor, "0.7.0.5").codes() == ["AlreadyApplied"]


# --- reporter ------------------------------------------------------------------------


def test_fingerprint_report() -> None:
    good = _descriptor([{"op": "insert-before", "fingerprint": '<script type="module"', "content": "<!--b-->", "marker": "<!--b-->"}])
    bad = _descriptor([{"op": "replace", "fingerprint": "/assets/", "content": "Y"}, {"op": "replace", "fingerprint": "zzz", "content": "Y"}])
    gated = _descriptor([{"op": "append-head", "content": "<x/>"}], appliesTo="<0.1.0")
    report = fingerprint_report(FAKE_INDEX_HTML, [good, bad, gated], "0.7.0.5")
    assert report.matched == ["kiro_crew/static/dist/index.html#ops/0"]
    assert report.missed == ["kiro_crew/static/dist/index.html#ops/0", "kiro_crew/static/dist/index.html#ops/1"]
    assert report.total == 3 and report.to_dict()["matched"] == 1
    assert report.details["kiro_crew/static/dist/index.html#ops/1"] == "miss"
    applied = apply_descriptor(FAKE_INDEX_HTML, good).text
    assert fingerprint_report(applied, [good]).details == {"kiro_crew/static/dist/index.html#ops/0": "already-applied"}


# --- import map (spike 1.4) ----------------------------------------------------------


def test_merge_import_map_is_idempotent_and_preserves_others() -> None:
    html, changed = merge_import_map(FAKE_INDEX_HTML, {"/assets/chunk-a-AAAA1111.js": "/apps/floofycrew/ui/patched/chunk-a.js"})
    assert changed
    _, parsed = find_import_map(html)
    assert parsed == {"imports": {"react": "/vendor/react.mjs", "/assets/chunk-a-AAAA1111.js": "/apps/floofycrew/ui/patched/chunk-a.js"}}
    again, changed = merge_import_map(html, {"/assets/chunk-a-AAAA1111.js": "/apps/floofycrew/ui/patched/chunk-a.js"})
    assert not changed and again == html
    removed, changed = merge_import_map(html, {"/assets/chunk-a-AAAA1111.js": None})
    assert changed and find_import_map(removed)[1] == {"imports": {"react": "/vendor/react.mjs"}}
    assert merge_import_map(html, {"never-there": None}) == (html, False)
    assert html.count('type="importmap"') == 1


def test_merge_import_map_inserts_when_absent_and_refuses_ambiguity() -> None:
    bare = '<head><script type="module" src="/assets/main.js"></script></head>'
    html, changed = merge_import_map(bare, {"a": "/b"})
    assert changed and html.index('type="importmap"') < html.index('type="module"')
    assert find_import_map(html)[1] == {"imports": {"a": "/b"}}
    assert merge_import_map("<head></head>", {}) == ("<head></head>", False)
    with pytest.raises(ValueError):
        merge_import_map("<head></head>", {"a": "/b"})
    with pytest.raises(ValueError):
        find_import_map('<script type="importmap">{}</script><script type="importmap">{}</script>')
    with pytest.raises(ValueError):
        find_import_map('<script type="importmap">{nope</script>')


def test_descriptor_import_map_block_is_applied() -> None:
    descriptor = _descriptor([{"op": "append-head", "content": "<x/>"}], importMap={"/assets/chunk-a-AAAA1111.js": "/apps/floofycrew/ui/patched/chunk-a.js"})
    result = apply_descriptor(FAKE_INDEX_HTML, descriptor)
    assert result.import_map_applied and result.changed
    assert "/apps/floofycrew/ui/patched/chunk-a.js" in result.text
    twice = apply_descriptor(result.text, descriptor)
    assert not twice.import_map_applied and twice.codes() == ["AlreadyApplied"]


def test_drop_modulepreload_any_attribute_order() -> None:
    html, changed = drop_modulepreload(FAKE_INDEX_HTML, "/assets/chunk-a-AAAA1111.js")
    assert changed and "modulepreload" not in html and '<script type="module"' in html
    assert drop_modulepreload(html, "/assets/chunk-a-AAAA1111.js") == (html, False)
    swapped = '<link href="/assets/x.js" rel="modulepreload">\n<link rel="modulepreload" href="/assets/y.js">'
    html, changed = drop_modulepreload(swapped, "/assets/x.js")
    assert changed and "x.js" not in html and "y.js" in html


@pytest.mark.skipif(not REAL_INDEX.is_file(), reason="real index.html copy not present under .scratch")
def test_real_shell_import_map_round_trip() -> None:
    real = REAL_INDEX.read_text(encoding="utf-8")
    _, parsed = find_import_map(real)
    assert "react" in parsed["imports"]
    html, changed = merge_import_map(real, {"/assets/useIsMobile-CVmE8oC-.js": "/apps/floofycrew/ui/patched/useIsMobile.js"})
    assert changed and html.count('type="importmap"') == 1
    _, merged = find_import_map(html)
    assert merged["imports"]["react"] == parsed["imports"]["react"]
    back, changed = merge_import_map(html, {"/assets/useIsMobile-CVmE8oC-.js": None})
    assert changed and find_import_map(back)[1] == parsed


# --- properties ----------------------------------------------------------------------

_token = st.text(alphabet="abcdefghij", min_size=3, max_size=8)
_content = st.text(alphabet="ABCDEFGHIJ", min_size=1, max_size=8)


@settings(max_examples=100, deadline=None)
@given(prefix=st.text(alphabet="xyz \n", max_size=20), suffix=st.text(alphabet="xyz \n", max_size=20), needle=_token, content=_content)
def test_single_literal_match_applies_and_is_idempotent(prefix: str, suffix: str, needle: str, content: str) -> None:
    text = prefix + needle + suffix  # disjoint alphabets: the needle occurs exactly once, the content not at all
    op = PatchOp("insert-after", content, fingerprint=needle, marker=content)
    descriptor = PatchDescriptor(target="t", ops=(op,))
    once = apply_descriptor(text, descriptor)
    assert once.applied and once.text == prefix + needle + content + suffix
    assert apply_descriptor(once.text, descriptor).codes() == ["AlreadyApplied"]


@settings(max_examples=100, deadline=None)
@given(count=st.integers(0, 4), needle=_token)
def test_match_count_other_than_one_skips_without_rewrite(count: int, needle: str) -> None:
    text = " ".join([needle] * count)
    descriptor = PatchDescriptor(target="t", ops=(PatchOp("replace", "Z", fingerprint=needle),))
    result = apply_descriptor(text, descriptor)
    if count == 1:
        assert result.text == "Z"
    else:
        assert result.text == text and result.codes() == ["FingerprintMiss" if count == 0 else "FingerprintAmbiguous"]
