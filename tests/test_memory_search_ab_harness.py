"""Tests for the search-only A/B retrievability harness (backlog H15).

The whole-pipeline scorer re-runs the nondeterministic extraction model on every
invocation, so a SEARCH-only change (which deterministically affects ONLY
retrievability) cannot be validated against the ~+/-0.03 extraction-noise floor.

The H15 harness added to ``scripts/memory_quality_score.py`` freezes the produced
memory set + Opus covering map ONCE and scores retrievability over that FIXED set
with whatever search code is on disk.  These tests pin the harness's core
guarantee: the score is DETERMINISTIC over a frozen set (so an A/B run against
two search-code versions isolates the search delta), the cache round-trips
through JSON (including int-coercion of covering_map keys), and the harness
writes nothing to ~/.ziya/memory.

Importing these symbols also fails on the pre-H15 script, so the test cannot pass
without the change.
"""

import pytest

from scripts.memory_quality_score import (
    build_retr_cache_record,
    save_retr_cache,
    load_retr_cache,
    score_retrievability_from_cache,
    capture_store_hashes,
)


def _chatA():
    # Single produced memory -> trivially top-3 for its query -> retrievable.
    return {
        "chat": {"chat_id": "chatA", "band": "short"},
        "ideal": [{"content": "the dashboard widget", "queries": ["dashboard widget"]}],
        "produced": [{"id": "pA", "content": "The dashboard widget renders the beam plan",
                      "layer": "architecture", "tags": []}],
        "covering_map": {0: 0},
    }


def _chatB():
    # Query shares no tokens with the produced memory -> not retrievable.
    return {
        "chat": {"chat_id": "chatB", "band": "short"},
        "ideal": [{"content": "unrelated", "queries": ["nonexistent quixotic incantation"]}],
        "produced": [{"id": "pB", "content": "notes about turtles and sunlight",
                      "layer": "domain_context", "tags": []}],
        "covering_map": {0: 0},
    }


def _chatC_empty_ideal():
    # |G|==0 -> retrievability undefined for this chat -> excluded from the mean.
    return {
        "chat": {"chat_id": "chatC", "band": "short"},
        "ideal": [],
        "produced": [{"id": "pC", "content": "whatever", "layer": "domain_context", "tags": []}],
        "covering_map": {},
    }


def _records(specs):
    return [
        build_retr_cache_record(s["chat"], s["ideal"], s["produced"], s["covering_map"])
        for s in specs
    ]


def test_frozen_set_scores_expected_retrievability(tmp_path):
    """A retrievable chat (1.0) + an unretrievable chat (0.0) + an empty-ideal
    chat (excluded) => aggregate retrievability 0.5 over the two scored chats."""
    records = _records([_chatA(), _chatB(), _chatC_empty_ideal()])
    result = score_retrievability_from_cache(records, sandbox_root=tmp_path / "sb")

    assert result["n_chats_total"] == 3
    assert result["n_chats_scored"] == 2          # empty-ideal chat excluded
    assert result["n_facts"] == 2
    assert result["n_facts_retrievable"] == 1
    assert result["retrievability"] == pytest.approx(0.5)


def test_score_is_deterministic_over_a_frozen_set(tmp_path):
    """THE H15 guarantee: with the produced set frozen, two scoring runs give an
    identical retrievability number — no extraction re-sampling in between. This
    is what lets an A/B run attribute any delta purely to the search code."""
    records = _records([_chatA(), _chatB(), _chatC_empty_ideal()])
    r1 = score_retrievability_from_cache(records, sandbox_root=tmp_path / "sb1")
    r2 = score_retrievability_from_cache(records, sandbox_root=tmp_path / "sb2")
    assert r1["retrievability"] == r2["retrievability"]
    assert [c["retrievability"] for c in r1["per_chat"]] == \
           [c["retrievability"] for c in r2["per_chat"]]


def test_cache_round_trips_and_coerces_int_covering_map_keys(tmp_path):
    """JSON stringifies covering_map keys; load_retr_cache must coerce them back
    to int, and scoring the reloaded cache must match scoring the in-memory one
    (a str-keyed covering_map would silently make every fact unretrievable)."""
    records = _records([_chatA(), _chatB()])
    path = tmp_path / "cache.json"
    save_retr_cache(path, records)

    loaded = load_retr_cache(path)
    for rec in loaded:
        assert all(isinstance(k, int) for k in rec["covering_map"].keys())

    before = score_retrievability_from_cache(records, sandbox_root=tmp_path / "a")
    after = score_retrievability_from_cache(loaded, sandbox_root=tmp_path / "b")
    assert before["retrievability"] == after["retrievability"]
    assert after["retrievability"] == pytest.approx(0.5)


def test_harness_does_not_write_the_real_store(tmp_path):
    """The harness scores over sandbox dirs only; the protected ~/.ziya/memory
    files must be byte-identical before and after (hard rule 1)."""
    records = _records([_chatA(), _chatB()])
    before = capture_store_hashes()
    score_retrievability_from_cache(records, sandbox_root=tmp_path / "sb")
    after = capture_store_hashes()
    assert before == after
