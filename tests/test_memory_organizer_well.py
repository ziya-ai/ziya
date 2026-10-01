"""
Organizer-side routes into (and data loss around) the mind-map gravity well.

  - _find_matching_node: tag overlap scaled by tag-set size, so a root
    that accreted 500+ tags no longer accepts every domain sharing two.
  - cleanup_corpus: an LLM "remove"/"merge" now ARCHIVES with the reason
    recorded on the memory.  It used to hard-delete; on 2026-09-25..26 the
    ~6-hourly runs removed 89 and merged 33 with no record of what or why,
    and the recovered set was mostly durable decisions and constraints.
Both fail against the previous organizer.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.memory.organizer import _find_matching_node, cleanup_corpus
from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture
def store(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


class TestFindMatchingNodeNormalised:

    def test_fat_root_does_not_absorb_domain_sharing_two_tags(self):
        fat = MindMapNode(id="fat", handle="Satellite Network Layout",
                          tags=["alpha", "beta"] + [f"f{i}" for i in range(510)])
        # raw score would be 2*3 = 6 >= 4 -> accepted before
        assert _find_matching_node("Editor Context Files",
                                   ["alpha", "beta", "x", "y", "z"], [fat]) is None

    def test_same_size_node_still_matches_on_two_tags(self):
        """Positive control: normalisation is a no-op when the node is not
        larger than the domain."""
        n = MindMapNode(id="n", handle="Something Else", tags=["alpha", "beta", "q", "r", "s"])
        assert _find_matching_node("Editor Context Files",
                                   ["alpha", "beta", "x", "y", "z"], [n]) == "n"


def _mk(store, content, tags):
    m = Memory(content=content, tags=tags)
    store.save(m)
    return m


@pytest.mark.asyncio
class TestCleanupArchives:

    async def test_remove_archives_with_reason_and_detaches(self, store):
        ms = [_mk(store, f"memory number {i}", ["t"]) for i in range(5)]
        store.save_mindmap_node(MindMapNode(id="d", handle="D", tags=["t"],
                                            memory_refs=[m.id for m in ms]))
        resp = json.dumps({"remove": [ms[1].id], "merge": [],
                           "reasons": {ms[1].id: "session artifact"}})
        with patch("app.services.model_resolver.call_service_model",
                   new_callable=AsyncMock, return_value=resp):
            result = await cleanup_corpus(store)
        assert result["removed"] == 1
        gone = store.get(ms[1].id)
        assert gone is not None, "cleanup must not hard-delete"
        assert gone.status == "archived"
        assert gone.archive_reason == "cleanup: session artifact"
        assert ms[1].id not in {m.id for m in store.list_memories(status="active")}
        assert ms[1].id not in store.get_mindmap_node("d").memory_refs
        assert ms[0].id in store.get_mindmap_node("d").memory_refs   # positive control

    async def test_merge_archives_absorbed_with_pointer(self, store):
        ms = [_mk(store, f"memory number {i}", ["t"]) for i in range(5)]
        resp = json.dumps({"remove": [], "reasons": {}, "merge": [
            {"keep": ms[0].id, "absorb": ms[2].id, "merged_content": "merged text here"}]})
        with patch("app.services.model_resolver.call_service_model",
                   new_callable=AsyncMock, return_value=resp):
            result = await cleanup_corpus(store)
        assert result["merged"] == 1
        assert store.get(ms[0].id).content == "merged text here"
        absorbed = store.get(ms[2].id)
        assert absorbed.status == "archived"
        assert absorbed.archive_reason == f"cleanup_merged_into:{ms[0].id}"
        assert absorbed.content == "memory number 2"      # original text kept
