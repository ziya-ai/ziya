"""
Tests for the reconsolidation labile-window mechanism.

When a memory is retrieved (loaded into context, or demonstrably used in
a response), it enters a transient "labile" state during which the
memory comparator is biased toward UPDATE over NOOP for partial overlaps.
Mirrors the biological reconsolidation window: retrieval briefly
destabilizes the trace, allowing finer corrections.

Covers:
  - mark_labile / is_labile pure mechanics
  - record_load opens the window
  - _apply_updates uses the longer window for "used" memories
  - _build_existing_listing surfaces the [recently retrieved] marker
  - Auto-cleanup of expired entries
"""
import time
from unittest.mock import patch

import pytest

from app.utils.memory_feedback import (
    mark_labile,
    is_labile,
    record_load,
    _apply_updates,
    _labile_until,
    _LABILE_RETRIEVAL_MS,
    _LABILE_USED_MS,
)
from app.utils.memory_comparator import _build_existing_listing


@pytest.fixture(autouse=True)
def _clean_labile_state():
    """Reset module-level labile state before each test."""
    _labile_until.clear()
    yield
    _labile_until.clear()


# -- mark_labile / is_labile -----------------------------------------

def test_mark_labile_opens_window():
    mark_labile(["m_abc"], 60_000)
    assert is_labile("m_abc") is True


def test_is_labile_false_for_unseen_id():
    assert is_labile("m_never_marked") is False


def test_is_labile_false_after_expiry():
    mark_labile(["m_abc"], 1_000)
    # Force expiry by rewinding the stored timestamp.
    _labile_until["m_abc"] = int(time.time() * 1000) - 1
    assert is_labile("m_abc") is False


def test_is_labile_auto_cleans_expired_entries():
    _labile_until["m_old"] = int(time.time() * 1000) - 1
    is_labile("m_old")  # triggers cleanup
    assert "m_old" not in _labile_until


def test_mark_labile_extends_with_max_not_min():
    """A shorter call cannot shrink an existing longer window."""
    mark_labile(["m_abc"], 10_000_000)  # long
    long_expiry = _labile_until["m_abc"]
    mark_labile(["m_abc"], 1_000)       # short — should not overwrite
    assert _labile_until["m_abc"] == long_expiry


def test_mark_labile_extends_when_new_window_longer():
    mark_labile(["m_abc"], 1_000)
    short_expiry = _labile_until["m_abc"]
    mark_labile(["m_abc"], 10_000_000)
    assert _labile_until["m_abc"] > short_expiry


def test_mark_labile_empty_input_is_noop():
    mark_labile([], 60_000)
    mark_labile(None, 60_000)
    assert _labile_until == {}


def test_mark_labile_skips_falsy_ids():
    mark_labile(["m_abc", None, "", "m_def"], 60_000)
    assert "m_abc" in _labile_until
    assert "m_def" in _labile_until
    assert None not in _labile_until
    assert "" not in _labile_until


def test_mark_labile_zero_duration_is_noop():
    mark_labile(["m_abc"], 0)
    assert _labile_until == {}


def test_is_labile_empty_string_returns_false():
    assert is_labile("") is False
    assert is_labile(None) is False


# -- record_load integration -----------------------------------------

def test_record_load_opens_windows_on_all_ids():
    record_load("conv-1", ["m_a", "m_b", "m_c"])
    assert is_labile("m_a") is True
    assert is_labile("m_b") is True
    assert is_labile("m_c") is True


def test_record_load_uses_retrieval_window_duration():
    before = int(time.time() * 1000)
    record_load("conv-1", ["m_a"])
    expiry = _labile_until["m_a"]
    # Window should be ~_LABILE_RETRIEVAL_MS from now (allow 1s skew).
    assert (expiry - before) >= _LABILE_RETRIEVAL_MS - 1000
    assert (expiry - before) <= _LABILE_RETRIEVAL_MS + 1000


def test_record_load_works_without_conversation_id():
    """Labile window is per-memory, independent of conversation tracking."""
    record_load(None, ["m_a"])
    assert is_labile("m_a") is True
    record_load("", ["m_b"])
    assert is_labile("m_b") is True


def test_record_load_empty_memory_ids_is_noop():
    record_load("conv-1", [])
    assert _labile_until == {}


# -- _apply_updates integration --------------------------------------

def test_apply_updates_opens_longer_window_for_used_ids():
    with patch("app.storage.memory.get_memory_storage") as mock_store:
        mock_store.return_value.get.return_value = None
        _apply_updates({"m_loaded", "m_used"}, used_ids={"m_used"})
    # Used memory: longer window
    assert is_labile("m_used") is True
    used_expiry = _labile_until["m_used"]
    now = int(time.time() * 1000)
    assert (used_expiry - now) >= _LABILE_USED_MS - 1000
    # Loaded-but-not-used: not opened by _apply_updates (record_load did that earlier).
    assert "m_loaded" not in _labile_until


def test_apply_updates_no_used_ids_does_not_open_windows():
    with patch("app.storage.memory.get_memory_storage") as mock_store:
        mock_store.return_value.get.return_value = None
        _apply_updates({"m_loaded"}, used_ids=set())
    assert _labile_until == {}


# -- _build_existing_listing integration -----------------------------

def test_build_existing_listing_marks_labile_memory():
    mark_labile(["m_abc"], 60_000)
    similar = [{"id": "m_abc", "layer": "domain_context", "content": "X"}]
    out = _build_existing_listing(similar)
    assert "[recently retrieved]" in out
    assert "[m_abc]" in out


def test_build_existing_listing_omits_marker_for_non_labile():
    similar = [{"id": "m_abc", "layer": "lexicon", "content": "X"}]
    out = _build_existing_listing(similar)
    assert "[recently retrieved]" not in out
    assert "[m_abc]" in out


def test_build_existing_listing_mixed_labile_and_non_labile():
    mark_labile(["m_hot"], 60_000)
    similar = [
        {"id": "m_hot", "layer": "decision", "content": "active"},
        {"id": "m_cold", "layer": "decision", "content": "inactive"},
    ]
    out = _build_existing_listing(similar)
    lines = out.split("\n")
    # The labile entry has the marker; the cold one does not.
    hot_line = next(l for l in lines if "m_hot" in l)
    cold_line = next(l for l in lines if "m_cold" in l)
    assert "[recently retrieved]" in hot_line
    assert "[recently retrieved]" not in cold_line


def test_build_existing_listing_empty_input():
    assert _build_existing_listing([]) == ""


def test_build_existing_listing_handles_missing_id():
    similar = [{"layer": "decision", "content": "no id field"}]
    out = _build_existing_listing(similar)
    assert "[?]" in out
