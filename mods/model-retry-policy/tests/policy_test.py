"""Unit tests for the policy brain (no gateway needed): ``python -m pytest tests/``."""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_policy():
    spec = importlib.util.spec_from_file_location("mrp_policy", ROOT / "hook" / "policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P = _load_policy()


def cfg(**over):
    return P.merged_config(over)


def profile(steps, primary=3, exhausted="stop", name="p"):
    return {"fallback": {"enabled": True, "profile": "p", "profiles": {"p": {"name": name, "primary_retries": primary, "steps": steps, "exhausted": exhausted}}}}


# --- config -------------------------------------------------------------------------------------------


def test_defaults_are_host_behaviour():
    c = cfg()
    assert P.is_host_behaviour(c)
    assert P.effective_retries(c) is None
    assert P.effective_delay(c, 1, 2.3) is None
    assert P.effective_chain(c) is None
    assert P.step_attempts(c, 0) is None
    assert P.preset_of(c) == "host"
    assert P.describe(c)["host_behaviour"] is True


def test_disabled_is_host_behaviour_whatever_else_is_set():
    c = cfg(enabled=False, retries={"mode": "infinite"}, delay={"mode": "fixed"}, **profile([{"model": "m"}]))
    assert P.is_host_behaviour(c)
    assert P.effective_retries(c) is None
    assert P.effective_delay(c, 3, 8.0) is None
    assert P.effective_chain(c) is None


@pytest.mark.parametrize("junk", [{}, "false", 0, [], None])
def test_only_an_explicit_false_disables(junk):
    # The page once wrote `enabled: {}` (a boolean spread through an object merge);
    # bool({}) is False, so every mechanism the user clicked read back as "host".
    c = cfg(enabled=junk, retries={"mode": "infinite"}, delay={"mode": "exponential"})
    assert c["enabled"] is True
    assert P.preset_of(c) == "infinite_exponential"
    assert P.effective_retries(c) == P.INFINITE


def test_merged_config_sanitises_everything():
    c = cfg(retries={"mode": "nope", "count": -4}, delay={"mode": "x", "interval_s": "abc", "factor": 99, "jitter": 3, "max_s": -1},
            fallback={"enabled": True, "profile": "missing", "profiles": {"a": {"steps": [{"model": "  "}, "bare-id", {"model": "with space"}, {"model": "ok", "retries": 0}], "exhausted": "wat", "primary_retries": "7"}}})
    assert c["retries"] == {"mode": "host", "count": 0}
    assert c["delay"]["mode"] == "host"
    assert c["delay"]["interval_s"] == 30.0
    assert c["delay"]["factor"] == 10.0
    assert c["delay"]["jitter"] == 1.0
    assert c["delay"]["max_s"] == 0.0
    # the profile referenced does not exist -> fallback off, profiles kept
    assert c["fallback"]["enabled"] is False and c["fallback"]["profile"] == ""
    a = c["fallback"]["profiles"]["a"]
    assert [s["model"] for s in a["steps"]] == ["bare-id", "ok"]
    assert a["steps"][0]["retries"] == P.HOST_CANDIDATE_ATTEMPTS
    assert a["steps"][1]["retries"] == 1
    assert a["exhausted"] == "stop" and a["primary_retries"] == 7 and a["name"] == "a"


def test_presets_round_trip_through_preset_of():
    for name, patch in P.PRESETS.items():
        c = cfg(**patch) if name != "fallback" else cfg(**profile([{"model": "m"}]))
        assert P.preset_of(c) == name, name


# --- mechanism 1: a retry count ------------------------------------------------------------------------------


def test_count_mode_sets_the_budget_and_caps_the_host_curve():
    c = cfg(retries={"mode": "count", "count": 12})
    assert P.effective_retries(c) == 12
    # delay mode still "host": the host's value is kept but bounded by max_s
    assert P.effective_delay(c, 1, 2.4) == 2.4
    assert P.effective_delay(c, 20, 2.0 * 2**19) == 300.0
    assert P.effective_chain(c) is None  # the host's agent.fallback_model chain still applies


def test_count_zero_means_no_same_model_retry():
    assert P.effective_retries(cfg(retries={"mode": "count", "count": 0})) == 0


# --- mechanism 2: infinite, fixed interval -----------------------------------------------------------------------


def test_infinite_fixed_interval():
    c = cfg(retries={"mode": "infinite"}, delay={"mode": "fixed", "interval_s": 45, "jitter": 0})
    assert P.effective_retries(c) == P.INFINITE
    for n in (1, 2, 50, 10**6):
        assert P.effective_delay(c, n, None) == 45.0


def test_fixed_interval_jitter_is_a_fraction_of_the_interval():
    c = cfg(retries={"mode": "infinite"}, delay={"mode": "fixed", "interval_s": 10, "jitter": 0.5})
    rng = random.Random(1)
    values = {P.effective_delay(c, 1, None, rng) for _ in range(50)}
    assert all(10.0 <= v <= 15.0 for v in values) and len(values) > 1


# --- mechanism 3: infinite, exponential backoff -------------------------------------------------------------------------


def test_infinite_exponential_backoff_is_bounded():
    c = cfg(retries={"mode": "infinite"}, delay={"mode": "exponential", "base_s": 1, "factor": 3, "max_s": 100, "jitter": 0})
    assert P.effective_retries(c) == P.INFINITE
    assert [P.effective_delay(c, n, None) for n in (1, 2, 3, 4, 5, 6)] == [1, 3, 9, 27, 81, 100]
    assert P.effective_delay(c, 10**6, None) == 100  # no OverflowError, just the cap


def test_attempt_below_one_is_treated_as_the_first():
    c = cfg(retries={"mode": "infinite"}, delay={"mode": "exponential", "base_s": 2, "factor": 2, "jitter": 0})
    assert P.effective_delay(c, 0, None) == 2.0 == P.effective_delay(c, -3, None)


# --- mechanism 4: fallback profiles ------------------------------------------------------------------------------------


def test_profile_owns_the_budget_and_the_chain():
    c = cfg(retries={"mode": "infinite"}, **profile([{"model": "a", "retries": 4}, {"model": "b", "retries": 1}], primary=2))
    # the profile's primary_retries wins over retries.mode
    assert P.effective_retries(c) == 2
    assert P.effective_chain(c) == ("a", "b")
    assert P.step_attempts(c, 0) == 4
    assert P.step_attempts(c, 1) == 1
    assert P.step_attempts(c, 5) == 1  # past the end with "stop": the last step's own count
    assert P.active_profile(c)["exhausted"] == "stop"
    assert P.preset_of(c) == "fallback"


def test_retry_last_keeps_the_last_candidate_forever():
    c = cfg(**profile([{"model": "a", "retries": 2}, {"model": "b", "retries": 3}], exhausted="retry-last"))
    assert P.step_attempts(c, 0) == 2
    assert P.step_attempts(c, 1) == P.INFINITE
    assert P.step_attempts(c, 7) == P.INFINITE


def test_retry_last_with_no_steps_is_infinite_same_model_retries():
    c = cfg(**profile([], exhausted="retry-last", primary=3))
    assert P.effective_retries(c) == P.INFINITE
    assert P.effective_chain(c) == ()
    assert P.step_attempts(c, 0) is None


def test_stop_with_no_steps_is_just_the_primary_budget():
    c = cfg(**profile([], exhausted="stop", primary=5))
    assert P.effective_retries(c) == 5
    assert P.effective_chain(c) == ()


def test_profile_with_host_delay_still_uses_the_host_curve_bounded():
    c = cfg(**profile([{"model": "a"}], primary=3))
    assert c["delay"]["mode"] == "host"
    assert P.effective_delay(c, 2, 4.4) == 4.4
    assert P.effective_delay(c, 30, 2.0 * 2**29) == 300.0


# --- the dashboard counter encoding --------------------------------------------------------------------------------------


def test_rewind_grants_exactly_the_requested_attempts():
    c = cfg(**profile([{"model": "a", "retries": 2}], primary=3))
    # budget 3: counter 2 -> one further same-model pass + the re-queued attempt = 2 attempts
    assert P.rewind_for(c, 2) == 2
    assert P.block_size(c, 2) == 2
    assert P.rewind_for(c, 1) == 3 and P.block_size(c, 3) == 1
    assert P.rewind_for(c, 4) == 0 and P.block_size(c, 0) == 4  # clamped: a block is at most budget+1
    assert P.rewind_for(c, P.INFINITE) == 0


def test_rewind_falls_back_to_the_host_budget_without_an_override():
    c = cfg()
    assert P.rewind_for(c, 2) == P.HOST_RETRIES - 1
    assert P.block_size(c, P.HOST_RETRIES - 1) == 2


# --- describe / journal --------------------------------------------------------------------------------------------------


def test_describe_summarises_the_policy():
    d = P.describe(cfg(retries={"mode": "infinite"}, delay={"mode": "fixed", "interval_s": 30, "jitter": 0.1}))
    assert d["retries"] == "infinite" and d["retries_value"] == P.INFINITE and d["midturn"] == "infinite"
    assert d["delay"].startswith("every 30 s") and "jitter 10 %" in d["delay"]
    assert d["chain"] is None and d["profile"] is None and d["preset"] == "infinite_fixed"
    d2 = P.describe(cfg(**profile([{"model": "x"}], name="Night shift", exhausted="retry-last")))
    assert d2["profile"] == "Night shift" and d2["chain"] == ["x"] and d2["exhausted"] == "retry-last" and d2["retries"] == "3"
    assert P.describe(cfg())["midturn"] == "host (1)"


@pytest.mark.parametrize(
    "over, expected",
    [
        ({}, None),                                                                        # host: the one-shot stays
        ({"enabled": False, "retries": {"mode": "infinite"}}, None),
        ({"retries": {"mode": "count", "count": 4}}, 4),
        ({"retries": {"mode": "count", "count": 0}}, 0),                                   # zero: the first mid-turn error is shown
        ({"retries": {"mode": "infinite"}}, P.INFINITE),
        (profile([{"model": "a", "retries": 3}, {"model": "b", "retries": 1}], primary=2), 6),   # every attempt the profile allows, on the current model
        (profile([{"model": "a", "retries": 3}], primary=2, exhausted="retry-last"), P.INFINITE),
        (profile([], primary=5, exhausted="stop"), 5),
    ],
)
def test_midturn_budget_follows_the_same_intent_as_the_ladder(over, expected):
    assert P.midturn_budget(cfg(**over)) == expected


def test_journal_records_counts_persists_and_bounds(tmp_path):
    path = str(tmp_path / "journal.json")
    j = P.Journal(path, keep=5, flush_every=0)
    for i in range(8):
        j.record(P.Event(ts=float(i), surface="dashboard", kind="retry", attempt=i))
    j.record(P.Event(ts=9.0, surface="unattended", kind="swap", model="b", previous="a"))
    j.record(P.Event(ts=10.0, surface="subagent", kind="exhausted", model="b"))
    j.record(P.Event(ts=11.0, surface="dashboard", kind="resume", attempt=1))
    j.flush(force=True)
    again = P.Journal(path, keep=5)
    assert len(again.recent()) == 5
    assert again.counters.retries == 8 and again.counters.swaps == 1 and again.counters.exhausted == 1 and again.counters.resumes == 1
    assert again.counters.by_surface == {"dashboard": 9, "unattended": 1, "subagent": 1}
    again.clear()
    again.flush(force=True)
    assert P.Journal(path).counters.retries == 0


def test_journal_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("{not json", encoding="utf-8")
    j = P.Journal(str(path))
    assert j.recent() == [] and j.counters.retries == 0


@pytest.mark.parametrize("bad", [None, 3, "x", [], {"retries": 1}])
def test_merged_config_accepts_garbage(bad):
    c = P.merged_config(bad)
    assert P.is_host_behaviour(c)
