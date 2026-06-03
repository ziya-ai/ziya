"""
Tests for the REM phase: synthesis (#1) and staleness detection (#3).

Covers:
  - _is_mature gate semantics (count, sources, age)
  - _should_synthesize cooldown via existing rem_synthesis memories
  - synthesize_node: pattern detected → proposal; null → no proposal
  - detect_staleness: contradiction gate; resurrection signal
  - rem_phase isolates per-node errors
  - Integration: contested in search results, contested excluded from prompt
"""
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.memory import Memory, MindMapNode
from app.utils.memory_rem import (
    _is_mature,
    _should_synthesize,
    synthesize_node,
    detect_staleness,
    rem_phase,
)


def _old_date(days_ago: int) -> str:
    """Return YYYY-MM-DD for N days ago."""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _make_memory(mid: str, content: str, layer: str = "domain_context",
                 created_days_ago: int = 60, learned_from: str = "auto_extraction",
                 importance: float = 0.5, status: str = "active",
                 retrieval_loaded_count: int = 1, tags=None) -> Memory:
    return Memory(
        id=mid, content=content, layer=layer,
        tags=tags or [],
        learned_from=learned_from,
        created=_old_date(created_days_ago),
        last_accessed=_old_date(0),
        status=status,
        importance=importance,
        retrieval_loaded_count=retrieval_loaded_count,
    )


def _make_store(memories, nodes=None):
    """Build a mock storage from a flat memory list and optional node list."""
    store = MagicMock()
    by_id = {m.id: m for m in memories}
    store.get.side_effect = lambda mid: by_id.get(mid)
    store.list_memories.side_effect = lambda status="active": [
        m for m in memories if (status is None or m.status == status)
    ]
    store.list_mindmap_nodes.return_value = nodes or []
    saved = []
    store.save.side_effect = lambda mem: saved.append(mem) or mem
    store._saved = saved
    return store


def _make_node(node_id: str, memory_ids, handle: str = "Test Domain") -> MindMapNode:
    return MindMapNode(id=node_id, handle=handle, memory_refs=list(memory_ids))


# -- _is_mature ------------------------------------------------------

def test_is_mature_rejects_few_memories():
    mems = [_make_memory(f"m{i}", f"c{i}") for i in range(3)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    mature, reason = _is_mature(node, store)
    assert mature is False
    assert "too_few_memories" in reason


def test_is_mature_rejects_single_source():
    mems = [_make_memory(f"m{i}", f"c{i}", learned_from="auto_extraction")
            for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    mature, reason = _is_mature(node, store)
    assert mature is False
    assert "too_few_sources" in reason


def test_is_mature_rejects_young_memories():
    mems = [_make_memory(f"m{i}", f"c{i}", created_days_ago=10,
                         learned_from="user_explanation" if i % 2 else "auto_extraction")
            for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    mature, reason = _is_mature(node, store)
    assert mature is False
    assert "too_young" in reason


def test_is_mature_accepts_qualifying_node():
    mems = [_make_memory(f"m{i}", f"c{i}", created_days_ago=60,
                         learned_from="user_explanation" if i % 2 else "auto_extraction")
            for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    mature, reason = _is_mature(node, store)
    assert mature is True
    assert reason == ""


def test_is_mature_ignores_inactive_memories():
    """Inactive memories don't count toward the maturity threshold."""
    mems = [_make_memory(f"m{i}", f"c{i}", created_days_ago=60,
                         learned_from="user_explanation" if i % 2 else "auto_extraction",
                         status="archived" if i < 2 else "active")
            for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    mature, reason = _is_mature(node, store)
    # Only 3 active memories — below threshold
    assert mature is False
    assert "too_few_memories" in reason


# -- _should_synthesize ---------------------------------------------

def test_should_synthesize_true_when_no_prior():
    mems = [_make_memory(f"m{i}", f"c{i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    assert _should_synthesize(node, store) is True


def test_should_synthesize_false_when_recent_synthesis_covers_node():
    source_ids = [f"m{i}" for i in range(5)]
    src_mems = [_make_memory(mid, f"src {i}") for i, mid in enumerate(source_ids)]
    syn = _make_memory("syn1", "abstracted principle", learned_from="rem_synthesis",
                       created_days_ago=5)
    syn.relations = {"elaborates": source_ids}  # 100% overlap
    node = _make_node("n1", source_ids)
    store = _make_store(src_mems + [syn])
    assert _should_synthesize(node, store) is False


def test_should_synthesize_true_when_synthesis_too_old():
    source_ids = [f"m{i}" for i in range(5)]
    src_mems = [_make_memory(mid, f"src {i}") for i, mid in enumerate(source_ids)]
    syn = _make_memory("syn1", "old principle", learned_from="rem_synthesis",
                       created_days_ago=60)  # outside cooldown
    syn.relations = {"elaborates": source_ids}
    node = _make_node("n1", source_ids)
    store = _make_store(src_mems + [syn])
    assert _should_synthesize(node, store) is True


def test_should_synthesize_true_when_overlap_below_threshold():
    source_ids = [f"m{i}" for i in range(5)]
    src_mems = [_make_memory(mid, f"src {i}") for i, mid in enumerate(source_ids)]
    syn = _make_memory("syn1", "partial principle", learned_from="rem_synthesis",
                       created_days_ago=5)
    syn.relations = {"elaborates": ["m0", "m1"]}  # 40% overlap, < 80%
    node = _make_node("n1", source_ids)
    store = _make_store(src_mems + [syn])
    assert _should_synthesize(node, store) is True


# -- synthesize_node -------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_node_creates_proposal_on_pattern():
    mems = [_make_memory(f"m{i}", f"fact {i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)

    fake_response = json.dumps({
        "synthesis": "All these memories show the user prefers explicit context curation.",
        "rationale": "Each memory describes a different mechanism for the same underlying preference."
    })
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)) as mock_call, \
         patch("app.storage.proposals.get_proposals_store") as mock_ps, \
         patch("app.utils.memory_extractor._next_activity_count", return_value=42):
        ps = MagicMock()
        added = []
        ps.add.side_effect = lambda p, activity_count=None: added.append(p)
        mock_ps.return_value = ps
        pid = await synthesize_node(node, store)

    assert pid is not None
    assert len(added) == 1
    proposal = added[0]
    assert proposal.learned_from == "rem_synthesis"
    assert "explicit context curation" in proposal.content
    # Source ids tunneled through conversation_id
    blob = json.loads(proposal.conversation_id)
    assert set(blob["rem_source_ids"]) == {"m0", "m1", "m2", "m3", "m4"}


@pytest.mark.asyncio
async def test_synthesize_node_returns_none_on_null_synthesis():
    mems = [_make_memory(f"m{i}", f"fact {i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    fake_response = json.dumps({"synthesis": None, "rationale": "no pattern"})
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)), \
         patch("app.storage.proposals.get_proposals_store") as mock_ps:
        pid = await synthesize_node(node, store)
    assert pid is None
    mock_ps.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_node_returns_none_on_too_short_synthesis():
    mems = [_make_memory(f"m{i}", f"fact {i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    fake_response = json.dumps({"synthesis": "short", "rationale": "x"})
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)), \
         patch("app.storage.proposals.get_proposals_store") as mock_ps:
        pid = await synthesize_node(node, store)
    assert pid is None
    mock_ps.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_node_handles_llm_error():
    mems = [_make_memory(f"m{i}", f"fact {i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(side_effect=RuntimeError("LLM down"))):
        pid = await synthesize_node(node, store)
    assert pid is None


@pytest.mark.asyncio
async def test_synthesize_node_handles_invalid_json():
    mems = [_make_memory(f"m{i}", f"fact {i}") for i in range(5)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value="not json at all")):
        pid = await synthesize_node(node, store)
    assert pid is None


# -- detect_staleness -----------------------------------------------

@pytest.mark.asyncio
async def test_staleness_flips_to_contested_with_contradiction():
    """LLM marks stale + newer memory contradicts → flip to contested."""
    target = _make_memory(
        "m_old", "system uses approach X for problem Y handling logic",
        created_days_ago=180, importance=0.7, retrieval_loaded_count=3,
    )
    # m_new must NOT land in the top-K candidate set — it's the
    # contradicting evidence that lives in context.  Low importance
    # keeps it out of the candidates.
    contradicting = _make_memory(
        "m_new", "system uses approach Z for problem Y handling logic now",
        created_days_ago=10, importance=0.1, retrieval_loaded_count=1,
    )
    other1 = _make_memory("m_other1", "unrelated detail about subsystem", importance=0.1, retrieval_loaded_count=1)
    other2 = _make_memory("m_other2", "another tangential note", importance=0.1, retrieval_loaded_count=1)
    mems = [target, contradicting, other1, other2]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    fake_response = json.dumps({
        "verdicts": [
            {"id": "m_old", "verdict": "false",
             "rationale": "superseded by m_new"}
        ]
    })
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)):
        contested = await detect_staleness(node, store)

    assert "m_old" in contested
    assert target.status == "contested"
    # save() must have been called for the contested memory
    saved_ids = [m.id for m in store._saved]
    assert "m_old" in saved_ids


@pytest.mark.asyncio
async def test_staleness_skipped_without_contradicting_evidence():
    """LLM marks stale but no contradicting memory → leave status alone."""
    target = _make_memory(
        "m_target", "completely unique fact about thing alpha and beta",
        importance=0.7, retrieval_loaded_count=3,
    )
    # All other memories are about totally different topics
    others = [_make_memory(f"m{i}", f"unrelated topic {i} kappa lambda mu",
                            retrieval_loaded_count=1) for i in range(4)]
    mems = [target, *others]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    fake_response = json.dumps({
        "verdicts": [{"id": "m_target", "verdict": "false",
                      "rationale": "feels stale"}]
    })
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)):
        contested = await detect_staleness(node, store)
    assert contested == []
    assert target.status == "active"


@pytest.mark.asyncio
async def test_staleness_skips_unknown_verdicts():
    target = _make_memory("m_target", "some content", importance=0.7,
                          retrieval_loaded_count=3)
    others = [_make_memory(f"m{i}", f"content {i}", retrieval_loaded_count=1)
              for i in range(4)]
    mems = [target, *others]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    fake_response = json.dumps({
        "verdicts": [{"id": "m_target", "verdict": "unknown", "rationale": "?"}]
    })
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value=fake_response)):
        contested = await detect_staleness(node, store)
    assert contested == []
    assert target.status == "active"


@pytest.mark.asyncio
async def test_staleness_skips_never_retrieved():
    """retrieval_loaded_count=0 means we've never validated — don't judge."""
    candidates_with_load = [_make_memory(f"m{i}", f"content {i}",
                                          importance=0.8, retrieval_loaded_count=0)
                             for i in range(4)]
    node = _make_node("n1", [m.id for m in candidates_with_load])
    store = _make_store(candidates_with_load)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value='{"verdicts":[]}')) as mock_call:
        contested = await detect_staleness(node, store)
    # No candidates pass the loaded_count filter, so no LLM call should happen
    assert contested == []
    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_staleness_skips_labile_memories():
    """Memories in their reconsolidation window are excluded."""
    target = _make_memory("m_target", "content", importance=0.9,
                          retrieval_loaded_count=3)
    others = [_make_memory(f"m{i}", f"content {i}", retrieval_loaded_count=1)
              for i in range(4)]
    mems = [target, *others]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    with patch("app.utils.memory_feedback.is_labile",
               side_effect=lambda mid: mid == "m_target"), \
         patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value='{"verdicts":[]}')) as mock_call:
        contested = await detect_staleness(node, store)
    # Target was filtered out by labile check, but we still have 3 candidates
    # remaining (with retrieval_loaded_count=1 each), so the LLM IS called.
    # What matters is m_target wasn't included.
    assert "m_target" not in contested
    if mock_call.called:
        sent_msg = mock_call.call_args.kwargs.get("user_message", "")
        assert "m_target" not in sent_msg


@pytest.mark.asyncio
async def test_staleness_only_top_k_evaluated():
    """Only top-3 by importance reach the LLM, regardless of node size."""
    mems = [_make_memory(f"m{i}", f"content {i}", importance=0.1 + i * 0.1,
                         retrieval_loaded_count=2)
            for i in range(8)]
    node = _make_node("n1", [m.id for m in mems])
    store = _make_store(mems)
    captured_msg = {}
    async def fake_call(**kwargs):
        captured_msg["user_message"] = kwargs.get("user_message", "")
        return '{"verdicts":[]}'
    with patch("app.services.model_resolver.call_service_model",
               new=fake_call):
        await detect_staleness(node, store)
    # Top-3 by importance: m7, m6, m5
    msg = captured_msg["user_message"]
    assert "[m7]" in msg
    assert "[m6]" in msg
    assert "[m5]" in msg
    assert "[m4]" not in msg


# -- rem_phase orchestration ----------------------------------------

@pytest.mark.asyncio
async def test_rem_phase_isolates_per_node_errors():
    """One node failing must not abort the rest."""
    good_mems = [_make_memory(f"g{i}", f"good {i}", created_days_ago=60,
                               learned_from="user_explanation" if i % 2 else "auto_extraction")
                 for i in range(5)]
    bad_mems = [_make_memory(f"b{i}", f"bad {i}", created_days_ago=60,
                              learned_from="user_explanation" if i % 2 else "auto_extraction")
                for i in range(5)]
    good_node = _make_node("good", [m.id for m in good_mems])
    bad_node = _make_node("bad", [m.id for m in bad_mems])
    store = _make_store(good_mems + bad_mems, nodes=[bad_node, good_node])

    call_count = {"n": 0}
    async def flaky_call(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first call boom")
        return json.dumps({"synthesis": None, "rationale": "no pattern"})

    with patch("app.services.model_resolver.call_service_model", new=flaky_call):
        result = await rem_phase(store)

    assert result["nodes_evaluated"] == 2
    # Both nodes should be evaluated even though the first crashed
    assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_rem_phase_skips_immature_nodes_silently():
    """Immature nodes don't trigger LLM calls or appear in summary counts."""
    young_mems = [_make_memory(f"y{i}", f"young {i}", created_days_ago=5)
                  for i in range(5)]
    young_node = _make_node("young", [m.id for m in young_mems])
    store = _make_store(young_mems, nodes=[young_node])
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value='{"synthesis": null}')) as mock_call:
        result = await rem_phase(store)
    assert result["nodes_evaluated"] == 1
    assert result["nodes_mature"] == 0
    assert result["syntheses_created"] == 0
    mock_call.assert_not_called()


# -- Integration: contested memory visibility -----------------------

def test_search_includes_contested_memories():
    """MemoryStorage.search() returns contested memories alongside active."""
    from app.storage.memory import MemoryStorage
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmpdir:
        ms = MemoryStorage(memory_dir=pathlib.Path(tmpdir))
        active = _make_memory("m_active", "approach for thing here", status="active")
        contested = _make_memory("m_contested", "approach for thing here also",
                                  status="contested")
        ms.save(active)
        ms.save(contested)
        results = ms.search("approach", limit=10)
        ids = {r.id for r in results}
        assert "m_active" in ids
        assert "m_contested" in ids


def test_list_memories_active_excludes_contested():
    """list_memories(status='active') excludes contested — system prompt path."""
    from app.storage.memory import MemoryStorage
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmpdir:
        ms = MemoryStorage(memory_dir=pathlib.Path(tmpdir))
        ms.save(_make_memory("m_active", "active content", status="active"))
        ms.save(_make_memory("m_contested", "contested content", status="contested"))
        active_only = ms.list_memories(status="active")
        ids = {m.id for m in active_only}
        assert "m_active" in ids
        assert "m_contested" not in ids