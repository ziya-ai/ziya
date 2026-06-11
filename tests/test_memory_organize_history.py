"""Tests for the bounded organize-history log."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.memory import organize_history as moh


@pytest.fixture
def history_dir(tmp_path):
    """Patch the home dir so the history file lives in tmp_path."""
    with patch("app.memory.organize_history.get_ziya_home" if False
               else "app.utils.paths.get_ziya_home", return_value=tmp_path):
        yield tmp_path


def test_append_writes_record(history_dir):
    moh.append_organize_result({
        "cleanup": {"removed": 5, "merged": 2, "reviewed": 50},
        "bootstrap": {"domains_created": 1, "domains_updated": 3, "memories_placed": 10},
        "relations": {"relations_found": 7},
        "cross_links": ["a", "b"],
        "divisions": ["c"],
        "rem": {"nodes_mature": 4, "syntheses_created": 1, "memories_contested": 0},
    })
    history = moh.load_organize_history()
    assert len(history) == 1
    record = history[0]
    assert record["cleanup"]["removed"] == 5
    assert record["bootstrap"]["domains_created"] == 1
    assert record["relations_found"] == 7
    assert record["cross_links_added"] == 2
    assert record["divisions"] == 1
    assert record["rem"]["syntheses_created"] == 1
    assert "timestamp" in record


def test_load_returns_newest_first(history_dir):
    for i in range(3):
        moh.append_organize_result({
            "cleanup": {"removed": i},
            "rem": {"syntheses_created": i},
        })
    history = moh.load_organize_history()
    assert len(history) == 3
    # Newest first: last-appended (i=2) should be index 0
    assert history[0]["cleanup"]["removed"] == 2
    assert history[2]["cleanup"]["removed"] == 0


def test_history_capped_at_50(history_dir):
    for i in range(60):
        moh.append_organize_result({"cleanup": {"removed": i}})
    history = moh.load_organize_history()
    assert len(history) == 50
    # Newest first: most recent is i=59
    assert history[0]["cleanup"]["removed"] == 59
    # Oldest retained is i=10 (60 total - 50 cap = drop first 10)
    assert history[-1]["cleanup"]["removed"] == 10


def test_append_swallows_malformed_existing_file(history_dir):
    path = history_dir / "memory" / "organize_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {{{")
    moh.append_organize_result({"cleanup": {"removed": 1}})
    # Should have replaced the broken file with a fresh single-entry list
    history = moh.load_organize_history()
    assert len(history) == 1


def test_load_returns_empty_list_when_no_file(history_dir):
    assert moh.load_organize_history() == []


def test_append_handles_missing_subkeys_gracefully(history_dir):
    """Real reorganize() may pass partial results when phases fail."""
    moh.append_organize_result({})  # nothing
    moh.append_organize_result({"cleanup": {}})  # empty subkey
    history = moh.load_organize_history()
    assert len(history) == 2
    # Defaults to 0 for all missing fields
    for record in history:
        assert record["cleanup"]["removed"] == 0
        assert record["rem"]["syntheses_created"] == 0
