"""Active-memory corroboration counts DISTINCT FOREIGN conversations.

Live 2026-11-10: one conversation re-extracting as it grew hit the comparator
UPDATE path four times in 30 minutes on m_76b1efe8, which ended at
corroborations=6 with no corroborating conversation recorded.  All three
increment sites in extractor.py (embedding dedup, UPDATE, ADD-with-similar)
incremented per event.  ProposalsStore already counts distinct foreign
conversations (tests/test_proposals_corroboration_distinct.py); these pin
the same rule for active memories, at the helper and through
run_post_conversation_extraction.
"""
import json
from unittest.mock import patch

import pytest

from app.memory.extractor import _credit_corroboration, run_post_conversation_extraction
from app.models.memory import Memory


def test_helper_credits_each_foreign_conversation_once():
    m = Memory(content="x", learned_from_conversation="origin")
    assert _credit_corroboration(m, "conv-a") is True
    assert _credit_corroboration(m, "conv-a") is False
    assert _credit_corroboration(m, "conv-b") is True
    assert m.corroborations == 2
    assert m.corroborated_by == ["conv-a", "conv-b"]


def test_helper_refuses_origin_and_unattributed():
    m = Memory(content="x", learned_from_conversation="origin")
    assert _credit_corroboration(m, "origin") is False
    assert _credit_corroboration(m, None) is False
    assert _credit_corroboration(m, "") is False
    assert m.corroborations == 0 and m.corroborated_by == []


def test_helper_caps_the_display_list():
    m = Memory(content="x")
    for i in range(7):
        _credit_corroboration(m, f"c{i}")
    assert m.corroborations == 7
    assert m.corroborated_by == [f"c{i}" for i in range(2, 7)]


def _setup(tmp_path):
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    store = MemoryStorage(memory_dir=tmp_path / "memory")
    proposals = ProposalsStore(memory_dir=tmp_path / "memory")
    existing = Memory(content="FCTS = Forward Channel Transport System",
                      layer="lexicon", tags=["fcts", "transport"],
                      learned_from_conversation="conv-origin")
    store.save(existing)
    return store, proposals, existing


def _messages(n):
    return [{"role": "user",
             "content": f"FCTS abbreviates Forward Channel Transport System "
                        f"(restating in turn {i}) with sufficient length to pass "
                        f"the stripping length gate."} for i in range(n)]


async def _extract(store, proposals, messages, conv, action):
    async def mock_call(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
        if category == "memory_comparison":
            return action
        # Wording varies per run, as real re-extraction does.  An identical
        # string would be dropped as an exact duplicate before the comparator
        # (after an UPDATE the stored text equals the candidate), so the
        # UPDATE path under test would never be reached.
        return json.dumps([{"content": f"FCTS abbreviates Forward Channel Transport System "
                                       f"(as stated in {conv} over {len(messages)} turns)",
                            "layer": "lexicon", "tags": ["fcts"], "confidence": "high"}])
    with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
         patch("app.storage.memory.get_memory_storage", return_value=store), \
         patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
         patch("app.services.model_resolver.call_service_model", side_effect=mock_call), \
         patch("app.memory.extractor.load_extracted_windows", return_value=set(), create=True), \
         patch("app.memory.extraction_cursor.load_extracted_windows", return_value=set()):
        return await run_post_conversation_extraction(messages, conv)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ['{"action": "ADD"}', None])
async def test_one_conversation_re_extracting_corroborates_once(tmp_path, action):
    store, proposals, existing = _setup(tmp_path)
    if action is None:
        action = json.dumps({"action": "UPDATE", "target_id": existing.id})
    # The same conversation, re-dispatched as it grows (server behaviour).
    first = await _extract(store, proposals, _messages(4), "conv-a", action)
    await _extract(store, proposals, _messages(6), "conv-a", action)
    after_a = store.get(existing.id)
    # Positive control: the path ran and credited the first event.
    assert first.get("corroborated", 0) + first.get("saved", 0) >= 1
    assert after_a.corroborations == 1, "same conversation credited twice"
    assert after_a.corroborated_by == ["conv-a"]
    # A second, foreign conversation still counts.
    await _extract(store, proposals, _messages(4), "conv-b", action)
    assert store.get(existing.id).corroborations == 2


@pytest.mark.asyncio
async def test_origin_conversation_does_not_corroborate_itself(tmp_path):
    store, proposals, existing = _setup(tmp_path)
    await _extract(store, proposals, _messages(4), "conv-origin", '{"action": "ADD"}')
    assert store.get(existing.id).corroborations == 0
