"""Comparator UPDATE must keep the superseded text and log the event.

Covers the two halves of the UPDATE-path change in app/memory/extractor.py:

  * the superseded row is archived under a fresh id, linked both ways, and
    invisible to every retrieval surface (search, active listing);
  * ``_log_update_event`` appends one JSONL line beside the store it changed
    (so the lifecycle simulation's sandbox gets its own file, never the live
    one) and the line carries ids/hashes/counters but no memory text.

These tests fail against unpatched code: ``_log_update_event`` and
``UPDATE_LOG_FILENAME`` do not exist there.
"""
import json
from pathlib import Path

import pytest

from app.models.memory import Memory


@pytest.fixture
def sandbox(tmp_path):
    from app.storage.memory import MemoryStorage
    # MemoryStorage embedding refresh is best-effort and tolerates no provider.
    return tmp_path, MemoryStorage(memory_dir=tmp_path)


def _supersede(store, live: Memory, new_content: str):
    """Mirror the archive-then-overwrite sequence the extractor performs."""
    superseded = Memory(**live.model_dump(exclude={"id"}))
    superseded.status = "archived"
    superseded.relations = {"superseded_by": [live.id]}
    live.relations.setdefault("supersedes", []).append(superseded.id)
    live.content = new_content
    store.save(live)
    store.save_many([superseded])
    return superseded


def test_archived_copy_is_linked_and_invisible(sandbox):
    tmp, store = sandbox
    live = store.save(Memory(content="Service X retries 3 times with 200ms backoff",
                             layer="architecture", corroborations=2,
                             retrieval_used_count=1))
    old = _supersede(store, live, "Service X retries 5 times with 500ms backoff")

    assert old.id != live.id
    assert store.get(live.id).content.startswith("Service X retries 5")
    archived = store.get(old.id)
    assert archived.status == "archived"
    assert archived.content.startswith("Service X retries 3")
    assert archived.corroborations == 2, "lineage counters travel with the archived row"
    assert archived.relations == {"superseded_by": [live.id]}
    assert store.get(live.id).relations["supersedes"] == [old.id]

    active_ids = {m.id for m in store.list_memories(status="active")}
    assert live.id in active_ids and old.id not in active_ids
    assert old.id not in {m.id for m in store.search("retries backoff", limit=10)}


def test_log_update_event_writes_beside_store_without_text(sandbox):
    from app.memory.extractor import _log_update_event, UPDATE_LOG_FILENAME

    tmp, store = sandbox
    live = store.save(Memory(content="SECRET-OLD-TEXT lives only in the store",
                             layer="decision", importance=0.7))
    old = _supersede(store, live, "SECRET-NEW-TEXT lives only in the store")

    _log_update_event(store, live, old, candidate={"layer": "decision"},
                      similar=[{"id": live.id, "_similarity": 0.91}],
                      conversation_id="conv-1")

    log = Path(tmp) / UPDATE_LOG_FILENAME
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["memory_id"] == live.id and rec["superseded_id"] == old.id
    assert rec["conversation_id"] == "conv-1"
    assert rec["old_layer"] == "decision" and rec["candidate_layer"] == "decision"
    assert rec["old_importance"] == 0.7
    assert rec["similar_count"] == 1 and rec["top_similarity"] == 0.91
    assert rec["old_hash"] != rec["new_hash"]
    # The log sits unencrypted next to memories.json: ids and hashes only.
    assert "SECRET-OLD-TEXT" not in lines[0] and "SECRET-NEW-TEXT" not in lines[0]
