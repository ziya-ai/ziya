"""The two Memory.relations writers are no longer invoked by the pipelines.

2026-11-09: Memory.relations never had a reader (no retrieval, injection or
REM gate consumes it) and a blind judge rated the writers' edges 2.5%
("contradicts") / 16% ("supports") precise, n=200 each.  reorganize's Phase 2
and on_memory_saved's step 4 were disabled; the functions themselves remain
callable so a stricter writer can be re-judged with the same harness.

Asserts the seam, not the halves: patch the writers to record calls, run the
real entry points with every other LLM phase neutralised, and check (a) the
writers were not called and (b) the result keys that organize_history and the
frontend read ("relations.relations_found", "linked") are still present.
"""
from unittest.mock import patch

import pytest

from app.memory import maintenance as mt
from app.memory import organizer
from app.models.memory import Memory
from app.storage.memory import MemoryStorage


def _store(tmp_path):
    return MemoryStorage(memory_dir=str(tmp_path / "mem"))


@pytest.mark.asyncio
async def test_reorganize_does_not_call_extract_all_relations(tmp_path):
    store = _store(tmp_path)
    store.save(Memory(id="m_1", content="alpha fact", layer="domain_context"))
    calls = []

    async def _record(*a, **k):
        calls.append(a)
        return {"status": "success", "relations_found": 99}

    async def _aget(*a, **k):
        return {}

    with patch.object(organizer, "extract_all_relations", _record), \
         patch("app.memory.organizer.cleanup_corpus", _aget), \
         patch("app.memory.organizer.bootstrap_mindmap", _aget), \
         patch("app.memory.rem.rem_phase", _aget), \
         patch("app.memory.organize_history.append_organize_result",
               lambda *a, **k: None):
        result = await organizer.reorganize(store)

    assert calls == [], "reorganize still invokes the relation writer"
    assert result["relations"]["status"] == "disabled"
    assert result["relations"]["relations_found"] == 0


def test_post_save_maintenance_does_not_call_auto_link(tmp_path):
    store = _store(tmp_path)
    mem = Memory(id="m_1", content="alpha fact", layer="domain_context")
    store.save(mem)
    calls = []

    def _record(*a, **k):
        calls.append(a)
        return [("m_1", "supports", "m_2")]

    with patch.object(mt, "_auto_link_by_embedding", _record), \
         patch.object(mt, "_enrich_related_tags", lambda *a, **k: []), \
         patch("app.storage.memory.get_memory_storage", lambda: store):
        results = mt.run_post_save_maintenance(mem.id)

    assert calls == [], "post-save maintenance still invokes the embedding auto-linker"
    assert results["linked"] == []
    assert mem.relations == {} or all(not v for v in mem.relations.values())
