"""
cleanup_corpus reviews each memory once, and its limits are enforced in code.

It used to re-judge the entire corpus on every organize run.  The
memory_organize system job fires about every 6h whenever any orphan exists
(217 runs on the live store by 2026-09-26), so each memory faced the LLM
judge ~4x a day indefinitely; even a small false-positive rate compounded
into the loss of most of one project's knowledge (172 -> 48 memories, 09-22 -> 09-26).

Pinned here:
  - a memory the judge kept is never sent to the judge again
  - decision / negative_constraint memories are never removed as junk,
    and absorbed only into a memory of the same layer
  - ids outside the batch are ignored; removals per batch are capped
  - a failed model call stamps nothing
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.memory.organizer import cleanup_corpus

# Literal, not imported, so the tests fail on behaviour against old code.
CLEANUP_REVIEW_FIELD = "cleanup_reviewed_at"
from app.models.memory import Memory
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


def _mk(store, content, layer="domain_context", **extra):
    m = Memory(content=content, layer=layer, tags=["t"], **extra)
    store.save(m)
    return m


def _run(store, response):
    mock = AsyncMock(return_value=json.dumps(response))
    return mock, patch("app.services.model_resolver.call_service_model", mock)


def _sent_ids(mock):
    return {line.split("]")[0].strip("[") for call in mock.call_args_list
            for line in call.kwargs["user_message"].splitlines() if line.startswith("[m_")}


@pytest.mark.asyncio
async def test_reviewed_memory_is_not_judged_again(store):
    old = [_mk(store, f"old fact {i}", **{CLEANUP_REVIEW_FIELD: 1}) for i in range(4)]
    new = _mk(store, "new fact")
    mock, p = _run(store, {"remove": [old[0].id], "merge": [], "reasons": {}})
    with p:
        result = await cleanup_corpus(store)
    assert _sent_ids(mock) == {new.id}                  # only the unreviewed one
    assert store.get(old[0].id).status == "active"      # out-of-batch id ignored
    assert result["reviewed"] == 1
    assert getattr(store.get(new.id), CLEANUP_REVIEW_FIELD)

    mock2, p2 = _run(store, {"remove": [], "merge": [], "reasons": {}})
    with p2:
        again = await cleanup_corpus(store)
    assert mock2.call_count == 0 and again["status"] == "nothing_new"


@pytest.mark.asyncio
async def test_protected_layers_are_not_removed_as_junk(store):
    d = _mk(store, "Chose credit-based flow control", layer="decision")
    n = _mk(store, "Static allocation wastes capacity", layer="negative_constraint")
    junk = _mk(store, "button padding tweak")
    rest = [_mk(store, f"filler {i}") for i in range(3)]
    _, p = _run(store, {"remove": [d.id, n.id, junk.id], "merge": [],
                        "reasons": {d.id: "stale", n.id: "vague", junk.id: "artifact"}})
    with p:
        await cleanup_corpus(store)
    assert store.get(d.id).status == "active"
    assert store.get(n.id).status == "active"
    assert store.get(junk.id).status == "archived"      # positive: removal still works


@pytest.mark.asyncio
async def test_protected_absorb_needs_same_layer(store):
    d1 = _mk(store, "Chose A for routing", layer="decision")
    d2 = _mk(store, "Routing uses A", layer="decision")
    ctx = _mk(store, "Routing context", layer="domain_context")
    d3 = _mk(store, "Chose B for queues", layer="decision")
    _mk(store, "filler")
    _, p = _run(store, {"remove": [], "reasons": {}, "merge": [
        {"keep": ctx.id, "absorb": d3.id, "merged_content": "merged ctx"},
        {"keep": d1.id, "absorb": d2.id, "merged_content": "Chose A for routing (all paths)"}]})
    with p:
        result = await cleanup_corpus(store)
    assert store.get(d3.id).status == "active"          # decision into context: refused
    assert store.get(d2.id).status == "archived"        # decision into decision: allowed
    assert result["merged"] == 1


@pytest.mark.asyncio
async def test_removals_are_capped_per_batch(store):
    ms = [_mk(store, f"fact number {i}") for i in range(8)]
    _, p = _run(store, {"remove": [m.id for m in ms], "merge": [], "reasons": {}})
    with p:
        result = await cleanup_corpus(store)
    assert result["removed"] == 2                       # ceil(8 * 0.25)
    assert sum(store.get(m.id).status == "active" for m in ms) == 6


@pytest.mark.asyncio
async def test_failed_call_stamps_nothing(store):
    ms = [_mk(store, f"fact {i}") for i in range(5)]
    with patch("app.services.model_resolver.call_service_model",
               AsyncMock(side_effect=Exception("model down"))):
        await cleanup_corpus(store)
    assert not any(getattr(store.get(m.id), CLEANUP_REVIEW_FIELD, None) for m in ms)
