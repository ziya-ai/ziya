"""Relevance-ranked core memories (policy C) in the live prompt path.

The always-injected core tier was degenerate: ``_select_core_memories``
filled its budget with priority layers in STORE ORDER (the same few
negative_constraints every turn, never used), and on the mind-map path --
the one a mature store runs on -- it injected nothing at all.  A paired
offline replay (scripts/replay_core_policy.py, 2026-11-09) put selection by
cosine to the conversation at 11-12% used@0.60 against 1-2% for store order.

Pinned here, outermost surface first:

  * the seam: ``PrecisionPromptSystem.build_messages`` hands the current
    question and recent human turns to the memory section, the ranked facts
    land on the final USER message, and two different questions produce an
    identical system block (the facts are per-turn, so they must stay out
    of the cached prefix);
  * the mind-map path, which previously injected no facts, now carries them;
  * selection is by max cosine over the query vectors, skips ``personal``,
    respects the budget, and never includes a memory with no embedding;
  * one embedding call per turn: repeated history turns hit the cache;
  * fallbacks: no query, Noop provider, or ``ZIYA_MEMORY_CORE_POLICY=store``
    give exactly the previous behaviour on both paths.
"""
import os
import sys
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.memory import Memory, MindMapNode  # noqa: E402
from app.storage.memory import MemoryStorage  # noqa: E402
from app.memory import prompt as mp  # noqa: E402


# -- fakes -------------------------------------------------------------------

class _Provider:
    """Deterministic 3-d embedder keyed by a word in the text; counts calls."""
    AXES = {"packet": 0, "memory": 1, "coffee": 2}

    def __init__(self):
        self.calls = []

    def embed_text(self, text):
        self.calls.append(text)
        v = np.zeros(3, dtype=np.float32)
        for word, i in self.AXES.items():
            if word in text.lower():
                v[i] = 1.0
        n = np.linalg.norm(v)
        return v / n if n else None


class _Cache:
    def __init__(self):
        self.vecs = {}

    def put(self, mid, v):
        self.vecs[mid] = v / np.linalg.norm(v)

    def get(self, mid):
        return self.vecs.get(mid)

    def search(self, query_vec, top_k=10, exclude_ids=None, include_ids=None):
        out = []
        for mid, v in self.vecs.items():
            if include_ids is not None and mid not in include_ids:
                continue
            if exclude_ids and mid in exclude_ids:
                continue
            s = float(np.dot(v, query_vec))
            if s > 0:
                out.append((mid, s))
        out.sort(key=lambda t: t[1], reverse=True)
        return out[:top_k]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_ENABLE_MEMORY", "true")
    monkeypatch.delenv("ZIYA_MEMORY_CORE_POLICY", raising=False)
    from app.mcp.builtin_tools import invalidate_category_cache
    invalidate_category_cache()
    mp._query_vec_cache.clear()
    store = MemoryStorage(memory_dir=tmp_path / "memory")
    provider, cache = _Provider(), _Cache()
    # Saving embeds through the module-level getters; route them to the fakes.
    patches = [
        mock.patch("app.storage.memory.get_memory_storage", return_value=store),
        mock.patch("app.services.embedding_service.get_embedding_provider", return_value=provider),
        mock.patch("app.services.embedding_service.get_embedding_cache", return_value=cache),
        mock.patch("app.services.embedding_service.embed_and_cache", return_value=None),
    ]
    for p in patches:
        p.start()
    mems = {
        "pkt": Memory(content="Packet forwarding drops frames above 9000 bytes", layer="architecture"),
        "mem": Memory(content="Memory proposals promote after two corroborations", layer="process"),
        "cof": Memory(content="User drinks coffee at 7am", layer="personal"),
        "neg": Memory(content="Never shuffle the store file order", layer="negative_constraint"),
    }
    for m in mems.values():
        store.save(m)
    cache.put(mems["pkt"].id, np.array([1.0, 0.0, 0.0]))
    cache.put(mems["mem"].id, np.array([0.0, 1.0, 0.0]))
    cache.put(mems["cof"].id, np.array([0.0, 0.0, 1.0]))
    # "neg" deliberately has no embedding.
    try:
        yield {"store": store, "provider": provider, "cache": cache, "mems": mems}
    finally:
        for p in reversed(patches):
            p.stop()
        invalidate_category_cache()


def _with_mindmap(env):
    store = env["store"]
    store.save_mindmap_node(MindMapNode(id="domain_net", handle="Networking", parent=None,
                                        tags=["packet"], memory_refs=[env["mems"]["pkt"].id]))
    return store


# -- selection ---------------------------------------------------------------

class TestSelection:

    def test_ranks_by_cosine_skips_personal_and_unembedded(self, env):
        q = env["provider"].embed_text("a packet question")
        core, extended = mp._select_core_memories_by_relevance(
            env["store"].list_memories(status="active"), [q])
        assert [m.content for m in core] == ["Packet forwarding drops frames above 9000 bytes"]
        assert extended == 3
        # A second query vector widens the ranking; max over vectors.
        q2 = env["provider"].embed_text("memory promotion")
        core, _ = mp._select_core_memories_by_relevance(
            env["store"].list_memories(status="active"), [q, q2])
        assert {m.content[:6] for m in core} == {"Packet", "Memory"}
        # Coffee is the best cosine match for this query but personal never injects.
        q3 = env["provider"].embed_text("coffee")
        core, _ = mp._select_core_memories_by_relevance(
            env["store"].list_memories(status="active"), [q3])
        assert core == []

    def test_budget_is_the_same_cost_model(self, env, monkeypatch):
        monkeypatch.setattr(mp, "CORE_TOKEN_BUDGET_CHARS", len("Packet forwarding drops frames above 9000 bytes") + 20)
        q = env["provider"].embed_text("packet memory")
        core, _ = mp._select_core_memories_by_relevance(
            env["store"].list_memories(status="active"), [q])
        assert len(core) == 1

    def test_query_texts_are_question_first_then_recent_human_turns(self):
        hist = [{"type": "human", "content": "h1"}, {"type": "ai", "content": "a1"},
                {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": "h2 text"}]},
                {"type": "ai", "content": "a2"}, {"type": "human", "content": "   "}]
        assert mp.core_query_texts("now?", hist) == ["now?", "h2 text", "h1"]
        long = "x" * (mp.CORE_QUERY_CHARS + 50)
        assert mp.core_query_texts(long, []) == ["x" * mp.CORE_QUERY_CHARS]
        many = [{"type": "human", "content": f"h{i}"} for i in range(20)]
        assert len(mp.core_query_texts("", many)) == mp.CORE_RELEVANCE_HISTORY_TURNS

    def test_repeated_turns_cost_one_embedding(self, env):
        texts = ["packet now", "memory before"]
        mp._query_vectors(texts)
        mp._query_vectors(["packet again"] + texts)
        assert env["provider"].calls == ["packet now", "memory before", "packet again"]


# -- the two prompt paths ----------------------------------------------------

class TestMindMapPath:

    def test_facts_appear_in_the_volatile_part_only(self, env):
        _with_mindmap(env)
        stable, volatile = mp.get_memory_prompt_sections(query_texts=["packet loss"], conversation_id="c1")
        assert "### Relevant Facts" in volatile
        assert "Packet forwarding drops frames" in volatile
        assert "Memory proposals promote" not in volatile
        assert "Domain Overview" in volatile                  # the index is still there
        assert "Packet forwarding" not in stable and "Relevant Facts" not in stable
        # The use-feedback loop knows what was injected.
        from app.memory.feedback import get_loaded_memory_ids
        assert env["mems"]["pkt"].id in get_loaded_memory_ids("c1")

    def test_no_query_keeps_the_previous_behaviour(self, env):
        _with_mindmap(env)
        _, volatile = mp.get_memory_prompt_sections()
        assert "Relevant Facts" not in volatile and "Packet forwarding" not in volatile

    def test_policy_store_disables(self, env, monkeypatch):
        _with_mindmap(env)
        monkeypatch.setenv("ZIYA_MEMORY_CORE_POLICY", "store")
        _, volatile = mp.get_memory_prompt_sections(query_texts=["packet loss"])
        assert "Relevant Facts" not in volatile

    def test_noop_provider_injects_nothing_and_does_not_fail(self, env):
        _with_mindmap(env)
        from app.services.embedding_service import NoopProvider
        with mock.patch("app.services.embedding_service.get_embedding_provider", return_value=NoopProvider()):
            _, volatile = mp.get_memory_prompt_sections(query_texts=["packet loss"])
        assert "Relevant Facts" not in volatile and "Domain Overview" in volatile


class TestFlatPath:

    def test_relevance_replaces_store_order(self, env):
        _, volatile = mp.get_memory_prompt_sections(query_texts=["memory promotion"])
        assert "### Known Facts (ranked by similarity to this turn)" in volatile
        assert "Memory proposals promote" in volatile
        assert "Never shuffle" not in volatile          # unembedded: not selected
        assert "3 additional memories available" in volatile

    def test_no_query_falls_back_to_store_order(self, env):
        _, volatile = mp.get_memory_prompt_sections()
        assert "### Known Facts\n" in volatile
        assert "Never shuffle" in volatile              # priority layer, store order
        assert "coffee" not in volatile


# -- the seam: the real prompt assembler -------------------------------------

class _FakeMsg:
    def __init__(self, type_, content):
        self.type, self.content = type_, content


class _FakePrompt:
    def format_messages(self, codebase="", tools="", question=""):
        return [_FakeMsg("system", "BASE"), _FakeMsg("human", question)]


def _build(question, history):
    from app.utils.precision_prompt_system import PrecisionPromptSystem
    with mock.patch("app.agents.agent.extract_codebase", return_value=""), \
         mock.patch("app.agents.prompts_manager.get_extended_prompt", return_value=_FakePrompt()), \
         mock.patch("app.utils.bead_prompt.get_bead_status_summary", return_value=""), \
         mock.patch("app.shadow.context.attached_sessions_tag", return_value=""):
        return PrecisionPromptSystem().build_messages(
            request_path="/api/chat",
            model_info={"model_name": "sonnet4.0", "model_family": "claude",
                        "endpoint": "bedrock", "model_id": "x"},
            files=[], question=question, chat_history=history,
            conversation_id="conv-core-c")


def _last_user(msgs):
    return next(m["content"] for m in reversed(msgs) if m["role"] == "user")


def test_assembler_ranks_by_the_current_turn_and_keeps_the_prefix_stable(env):
    _with_mindmap(env)
    hist = [{"type": "human", "content": "about memory promotion"}, {"type": "ai", "content": "ok"}]
    a = _build("why do packets drop?", hist)
    b = _build("how do coffee breaks work?", hist)
    ta, tb = _last_user(a), _last_user(b)
    assert "Packet forwarding drops frames" in ta
    assert "Packet forwarding drops frames" not in tb
    # The history turn is part of the query on both turns.
    assert "Memory proposals promote" in ta and "Memory proposals promote" in tb
    assert "drinks coffee at 7am" not in tb                   # personal: never injected
    assert "<MemoryOverview>" in ta and ta.index("<MemoryOverview>") > ta.index("why do packets drop?")
    # Per-turn facts never touch the cached system block.
    assert a[0]["content"] == b[0]["content"]
    assert "Packet forwarding" not in a[0]["content"]
    # The use-feedback loop is keyed by the caller's real conversation id.
    from app.memory.feedback import get_loaded_memory_ids
    assert env["mems"]["pkt"].id in get_loaded_memory_ids("conv-core-c")
    # The guidance in the stable block describes what the overview now carries.
    assert "ranked by similarity to the current turn" in a[0]["content"]
