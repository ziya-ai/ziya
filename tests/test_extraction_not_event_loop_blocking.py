"""run_post_conversation_extraction must not embed on the event loop.

The stall watchdog caught the loop here (2026-10-09):

    extractor.run_post_conversation_extraction
      -> proposals_store.add -> embed_and_cache -> bedrock invoke_model
      -> ssl.read

``ProposalsStore.add`` and ``MemoryStorage.save`` are synchronous and each
makes a blocking embedding round trip (``embed_and_cache``).  The extractor
is a coroutine on the server loop, so every such call must go through
``asyncio.to_thread``.  tests/test_lifecycle_not_event_loop_blocking.py
covers the lifecycle sweep that runs at the end of the same function.

Behavioural: the real orchestration runs against sandbox stores with
``embed_and_cache`` swapped for a probe that records its thread.  Against
the pre-fix extractor the probe runs on the loop (main) thread.
"""
import json
import threading
from unittest.mock import patch

import pytest

from app.memory.extractor import (
    run_post_conversation_extraction,
    TRIAGE_SYSTEM_PROMPT,
)


class _EmbedProbe:
    def __init__(self):
        self.calls = []   # (memory_id, thread)

    def __call__(self, memory_id, content):
        self.calls.append((memory_id, threading.current_thread()))
        return None

    def ids(self, prefix):
        return [mid for mid, _ in self.calls if mid.startswith(prefix)]

    def on_loop(self):
        return [mid for mid, t in self.calls if t is threading.main_thread()]


def _stores(tmp_path):
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    return (MemoryStorage(memory_dir=tmp_path / "memory"),
            ProposalsStore(memory_dir=tmp_path / "memory"))


# Zero user-salience conversation admitted by a triage hint; same fixture
# shape as tests/test_memory_admission_triage.py.
_MESSAGES = [
    {"role": "user",
     "content": "Can you show me the widget dashboard layout in some detail, "
                "walking through what each region of the screen displays?"},
    {"role": "assistant",
     "content": "The widget dashboard renders its panels through the Grafana "
                "embed API, and the dashboard refresh interval is fixed at thirty "
                "seconds by the frobnicator service on port 8080."},
    {"role": "user",
     "content": "And where does the panel data itself come from behind the scenes?"},
    {"role": "assistant",
     "content": "Panel data for the widget dashboard is served by the frobnicator "
                "service, which aggregates the metrics and exposes them to the "
                "Grafana embed API for rendering at the thirty second cadence."},
    {"role": "user",
     "content": "Okay, that walkthrough of the widget dashboard is helpful, "
                "appreciate you laying out how the whole layout fits together."},
]

_HINT_JSON = json.dumps({"candidates": [
    {"gist": "widget dashboard refresh interval is thirty seconds via the frobnicator service",
     "layer": "architecture", "quote": "refresh interval is fixed at thirty seconds"},
]})

_CANDIDATE = {
    "content": "The widget dashboard refresh interval is thirty seconds, set by "
               "the frobnicator service.",
    "layer": "architecture", "tags": ["widget", "dashboard"], "confidence": "high",
}


def _dispatch(comparison):
    async def mock_call(category, system_prompt, user_message,
                        max_tokens=2048, temperature=0.2):
        if category == "memory_comparison":
            return comparison
        if system_prompt == TRIAGE_SYSTEM_PROMPT:
            return _HINT_JSON
        return json.dumps([_CANDIDATE])
    return mock_call


async def _run(messages, store, proposals, probe, mock_call=None, **kw):
    patches = [
        patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True),
        patch("app.storage.memory.get_memory_storage", return_value=store),
        patch("app.storage.proposals.get_proposals_store", return_value=proposals),
        patch("app.services.embedding_service.embed_and_cache", probe),
    ]
    if mock_call is not None:
        patches.append(patch("app.services.model_resolver.call_service_model",
                             side_effect=mock_call))
    for p in patches:
        p.start()
    try:
        return await run_post_conversation_extraction(messages, **kw)
    finally:
        for p in reversed(patches):
            p.stop()


def _seed_similar(store):
    from app.models.memory import Memory
    return store.save(Memory(
        content="The widget dashboard refresh interval is twenty seconds, set by "
                "the frobnicator service.",
        layer="architecture", tags=["widget", "dashboard"], status="active",
    ))


@pytest.mark.asyncio
async def test_reference_proposal_embeds_off_the_loop(tmp_path):
    store, proposals = _stores(tmp_path)
    probe = _EmbedProbe()
    msgs = [
        {"role": "user", "content": "look at https://example.com/architecture for background"},
        {"role": "assistant", "content": "Got it, will reference."},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "You're welcome."},
    ]
    result = await _run(msgs, store, proposals, probe,
                        conversation_id="c-ref", project_path="/tmp/proj")
    assert result.get("references", 0) == 1
    assert probe.ids("prop_"), "reference proposal was never embedded -- seam not exercised"
    assert probe.on_loop() == [], f"embedded on the event loop: {probe.on_loop()}"


@pytest.mark.asyncio
async def test_extracted_proposal_embeds_off_the_loop(tmp_path):
    store, proposals = _stores(tmp_path)
    probe = _EmbedProbe()
    result = await _run(_MESSAGES, store, proposals, probe, _dispatch('{"action": "ADD"}'),
                        conversation_id="c-add")
    assert result.get("proposed", 0) >= 1
    assert probe.ids("prop_"), "extracted proposal was never embedded -- seam not exercised"
    assert probe.on_loop() == [], f"embedded on the event loop: {probe.on_loop()}"


@pytest.mark.asyncio
async def test_update_save_embeds_off_the_loop(tmp_path):
    store, proposals = _stores(tmp_path)
    target = _seed_similar(store)
    probe = _EmbedProbe()
    comparison = json.dumps({"action": "UPDATE", "target_id": target.id})
    await _run(_MESSAGES, store, proposals, probe, _dispatch(comparison),
               conversation_id="c-upd")
    assert store.get(target.id).content == _CANDIDATE["content"], "UPDATE path did not run"
    assert target.id in probe.ids("m"), "updated memory was never re-embedded"
    assert probe.on_loop() == [], f"embedded on the event loop: {probe.on_loop()}"


@pytest.mark.asyncio
async def test_update_embeds_new_content_exactly_once(tmp_path):
    """store.save re-embeds on a content change; a second explicit
    embed_and_cache after it was a duplicate Bedrock round trip."""
    store, proposals = _stores(tmp_path)
    target = _seed_similar(store)
    probe = _EmbedProbe()
    comparison = json.dumps({"action": "UPDATE", "target_id": target.id})
    await _run(_MESSAGES, store, proposals, probe, _dispatch(comparison),
               conversation_id="c-upd-once")
    assert store.get(target.id).content == _CANDIDATE["content"], "UPDATE path did not run"
    n = probe.ids("m").count(target.id)
    assert n == 1, f"updated memory embedded {n} times"


@pytest.mark.asyncio
async def test_add_corroboration_save_embeds_off_the_loop(tmp_path):
    store, proposals = _stores(tmp_path)
    target = _seed_similar(store)
    probe = _EmbedProbe()
    await _run(_MESSAGES, store, proposals, probe, _dispatch('{"action": "ADD"}'),
               conversation_id="c-corr")
    assert (store.get(target.id).corroborations or 0) == 1, "corroboration path did not run"
    assert target.id in probe.ids("m"), "corroborated memory save was never embedded"
    assert probe.on_loop() == [], f"embedded on the event loop: {probe.on_loop()}"
