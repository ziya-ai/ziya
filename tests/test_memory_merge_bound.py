"""
Corpus-cleanup merges must be dedups, not concatenations.

On the live store (2026-09-26) memory m_9c41d36d had grown to 2,777 chars /
146 tags: ~15 unrelated facts plus test-venue state ("Rigs 0 and 3 are
currently down; rigs 1 and 2 are live").  Extraction caps content at 500 chars
and 4 tags, and nothing else concatenates content, so the growth came from
organizer.cleanup_corpus applying the model's merged_content verbatim and
unioning tags without a cap, run after run.

Also covers scripts/split_fused_memories.py, which repairs the memories that
already grew: its effect is asserted through a real MemoryStorage.

The TestMergeBound cases fail against the unbounded organizer.
"""
import importlib.util
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.models.memory import Memory, MemoryScope
from app.storage.memory import MemoryStorage

_spec = importlib.util.spec_from_file_location(
    "split_fused", os.path.join(os.path.dirname(__file__), "..", "scripts",
                                "split_fused_memories.py"))
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)

K = "/w/projb-core"


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture
def store(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _mk(store, content, tags, project=None):
    m = Memory(content=content, tags=tags,
               scope=MemoryScope(project_paths=[project] if project else []))
    store.save(m)
    return m


async def _cleanup(store, merge):
    from app.memory.organizer import cleanup_corpus
    resp = json.dumps({"remove": [], "reasons": {}, "merge": merge})
    with patch("app.services.model_resolver.call_service_model",
               new_callable=AsyncMock, return_value=resp):
        return await cleanup_corpus(store)


@pytest.mark.asyncio
class TestMergeBound:

    async def test_concatenating_merge_is_rejected(self, store):
        a = _mk(store, "The decap path uses ECMP to the NPU to port 8 to the FPGA to the shaper.", ["decap"])
        b = _mk(store, "The FPGA DRAM vFIFO buffer depth is about 1.6 to 2.0 MB.", ["vfifo"])
        for i in range(3):
            _mk(store, f"filler memory {i}", ["f"])
        result = await _cleanup(store, [{"keep": a.id, "absorb": b.id,
                                         "merged_content": a.content + " " + b.content}])
        assert result["merged"] == 0 and result["merges_rejected"] == 1
        assert store.get(a.id).content == a.content
        assert store.get(b.id).status == "active"      # a distinct fact is not lost

    async def test_dedup_merge_still_applies_and_caps_tags(self, store):
        """Positive control: a real dedup merges, and the tag union is capped."""
        a = _mk(store, "The pacer protects the uplink from overdrive.", ["pacer", "a", "b", "c"])
        b = _mk(store, "The pacer keeps the uplink from being overdriven.", ["x", "y", "z", "w"])
        for i in range(3):
            _mk(store, f"filler memory {i}", ["f"])
        result = await _cleanup(store, [{"keep": a.id, "absorb": b.id,
                                         "merged_content": "The pacer protects the uplink from overdrive."}])
        assert result["merged"] == 1
        assert store.get(b.id).status == "archived"
        from app.memory.organizer import MERGE_MAX_TAGS
        assert len(store.get(a.id).tags) == MERGE_MAX_TAGS

    async def test_nothing_merges_into_an_already_fused_memory(self, store):
        blob = _mk(store, "x" * 2777, ["t"])
        b = _mk(store, "Rigs 0 and 3 are currently down; rigs 1 and 2 are live.", ["t"])
        for i in range(3):
            _mk(store, f"filler memory {i}", ["f"])
        result = await _cleanup(store, [{"keep": blob.id, "absorb": b.id,
                                         "merged_content": "x" * 2777 + " " + b.content}])
        assert result["merged"] == 0
        assert len(store.get(blob.id).content) == 2777


class TestSplitFused:

    PARTS = [
        {"content": "The ingress decap path runs ECMP, NPU, port 8, FPGA, interconnect, then shaper.",
         "layer": "architecture", "tags": ["decap", "per"], "keep": True,
         "atomicity": 0.9, "self_containment": 0.9, "durability": 0.9},
        {"content": "Rigs 0 and 3 are currently down; rigs 1 and 2 are live.", "keep": False,
         "drop_reason": "current venue state"},
        {"content": "Calls `a_fn` then `b_fn` then `c_fn` in the loop body here.",
         "layer": "architecture", "tags": ["x"], "keep": True,
         "atomicity": 0.9, "self_containment": 0.9, "durability": 0.9},
    ]

    def test_plan_separates_keep_drop_and_gate(self):
        plan = sf.plan_split(self.PARTS, "domain_context")
        assert [c["content"][:14] for c in plan["keep"]] == ["The ingress de"]
        assert plan["dropped"][0][1] == "current venue state"
        assert len(plan["gate_rejected"]) == 1

    def test_apply_archives_original_and_ephemeral_is_gone(self, store):
        orig = _mk(store, "fused " * 200 + "Rigs 0 and 3 are currently down.", ["projb"], K)
        plan = sf.plan_split(self.PARTS[:2], orig.layer)
        new = sf.build_memories(orig, plan["keep"])
        reason = sf.apply_split(store, orig, new)

        assert reason == f"split_into:{new[0].id}"
        assert store.get(orig.id).status == "archived"
        active = store.list_memories(status="active")
        assert [m.id for m in active] == [new[0].id]
        assert new[0].scope.project_paths == [K] and new[0].related == [orig.id]
        assert not any("currently down" in m.content for m in active)
        assert store.search("decap path shaper")[0].id == new[0].id

    def test_parse_parts_tolerates_fences_and_junk(self):
        raw = "```json\n" + json.dumps([{"content": "a fact"}, {"content": ""}, 3]) + "\n```"
        assert [p["content"] for p in sf.parse_parts(raw)] == ["a fact"]
        assert sf.parse_parts("nope") == []
