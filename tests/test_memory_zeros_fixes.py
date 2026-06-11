"""
Regression suite for the "Memory Browser shows zeros" investigation.

Covers the full set of fixes made while diagnosing why the memory browser
displayed zeros for usage counters and why contested memories were invisible:

  1. context.set_conversation_id / get_conversation_id_or_none round-trip
     (the orphaned-dead-code bug that made get_conversation_id_or_none always None)
  2. conversation_id injected into builtin-tool args in tool_execution.py
  3. MemoryStorage.list_memories + get() restored (diff-application deletion)
  4. MemoryStorage.save_many batch write semantics
  5. MemorySearchTool decay throttle (once / 10 min) + batched archive
  6. MemorySearchTool result-touch uses one batched write
  7. memory_feedback._prune_stale_state leak bounds
  8. memory_feedback.apply_feedback non-blocking (asyncio.to_thread) + batched _apply_updates
  9. cold-cache semantic-search skip is a real skip (else branch)
 10. /api/v1/memory/all includes contested memories
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.memory import Memory, MemoryProposal
from app.storage.memory import MemoryStorage


# ===========================================================================
# 1. context.py — set_conversation_id was a no-op (empty body) and the real
#    .set() line was orphaned dead code after another function's return.
# ===========================================================================

class TestConversationIdContextVar:

    def _reset(self):
        import app.context as ctx
        # ContextVars have no clear(); set to None so leakage between tests
        # is deterministic.
        ctx._request_conversation_id.set(None)

    def test_set_then_get_roundtrip(self):
        import app.context as ctx
        self._reset()
        ctx.set_conversation_id("conv-abc")
        assert ctx.get_conversation_id_or_none() == "conv-abc"

    def test_default_is_none(self):
        self._reset()
        import app.context as ctx
        assert ctx.get_conversation_id_or_none() is None

    def test_overwrite(self):
        import app.context as ctx
        self._reset()
        ctx.set_conversation_id("first")
        ctx.set_conversation_id("second")
        assert ctx.get_conversation_id_or_none() == "second"

    def test_no_orphaned_dead_code_after_iteration_ctx(self):
        """The .set() line must live inside set_conversation_id, not after
        get_task_iteration_context's return (the original corruption)."""
        import inspect
        import app.context as ctx
        src = inspect.getsource(ctx.set_conversation_id)
        assert "_request_conversation_id.set(conversation_id)" in src
        # And get_task_iteration_context must NOT contain the stray line.
        itc_src = inspect.getsource(ctx.get_task_iteration_context)
        assert "_request_conversation_id.set" not in itc_src

    def test_set_conversation_id_is_not_empty_body(self):
        """Guard against re-introducing the empty-body regression."""
        import app.context as ctx
        self._reset()
        # If the body were just a docstring, this would silently no-op.
        ctx.set_conversation_id("real-value")
        assert ctx.get_conversation_id_or_none() == "real-value"


# ===========================================================================
# 2. tool_execution.py — conversation_id must be injected into builtin tool
#    args so memory tools can call record_load with the real conversation_id.
# ===========================================================================

class _RecordingTool:
    """Fake builtin tool that captures the kwargs it was executed with."""
    name = "memory_search"
    description = "fake memory search tool for testing"
    is_internal = False

    def __init__(self):
        self.captured_kwargs = None

    async def execute(self, **kwargs):
        self.captured_kwargs = dict(kwargs)
        return {"content": "ok", "count": 0}


def _make_builtin_ctx(direct_tool, **overrides):
    from app.tool_execution import ToolExecContext
    defaults = dict(
        tool_id="toolu_x",
        tool_name="memory_search",
        actual_tool_name="memory_search",
        args={"query": "obp"},
        all_tools=[direct_tool],
        internal_tool_names=set(),
        mcp_manager=AsyncMock(),
        project_root="/proj/root",
        conversation_id="conv-xyz",
        conversation=[],
        recent_commands=[],
        inter_tool_delay={"current": 0.0, "min": 0.0, "decay_factor": 0.9},
        iteration_start_time=0.0,
        track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [],
        executor=MagicMock(),
    )
    defaults.update(overrides)
    return ToolExecContext(**defaults)


def _wrap_direct(tool_instance):
    from app.mcp.enhanced_tools import DirectMCPTool
    return DirectMCPTool(tool_instance)


async def _run_builtin(ctx):
    from app.tool_execution import execute_single_tool
    events = []
    # Patch the signing/audit/sanitize chain so the builtin path runs clean.
    with patch("app.mcp.signing.verify_tool_result", return_value=(True, None)), \
         patch("app.mcp.signing.strip_signature_metadata", side_effect=lambda r: r), \
         patch("app.mcp.signing.sign_tool_result", side_effect=lambda *a, **k: a[2]), \
         patch("app.server.record_verification_result"), \
         patch("app.utils.tool_audit_log.log_tool_execution"), \
         patch("app.utils.tool_result_sanitizer.sanitize_for_context", side_effect=lambda t, **k: t):
        async for evt in execute_single_tool(ctx):
            events.append(evt)
    return events


class TestConversationIdInjection:

    @pytest.mark.asyncio
    async def test_builtin_receives_conversation_id(self):
        rec = _RecordingTool()
        ctx = _make_builtin_ctx(_wrap_direct(rec), conversation_id="conv-xyz")
        mock_ex = ctx.executor
        mock_ex._get_tool_header.return_value = "Memory"
        mock_ex._infer_syntax_hint.return_value = ""
        mock_ex._format_tool_result.return_value = "ok"
        await _run_builtin(ctx)
        assert rec.captured_kwargs is not None
        assert rec.captured_kwargs.get("conversation_id") == "conv-xyz"

    @pytest.mark.asyncio
    async def test_builtin_receives_workspace_path(self):
        """Regression guard: the pre-existing _workspace_path injection
        must still coexist with the new conversation_id injection."""
        rec = _RecordingTool()
        ctx = _make_builtin_ctx(_wrap_direct(rec), project_root="/proj/root")
        mock_ex = ctx.executor
        mock_ex._get_tool_header.return_value = "Memory"
        mock_ex._infer_syntax_hint.return_value = ""
        mock_ex._format_tool_result.return_value = "ok"
        await _run_builtin(ctx)
        assert rec.captured_kwargs.get("_workspace_path") == "/proj/root"
        assert rec.captured_kwargs.get("conversation_id") == "conv-xyz"

    @pytest.mark.asyncio
    async def test_no_conversation_id_not_injected(self):
        """When conversation_id is None, the key must not be force-added
        (the tool's own default handling applies)."""
        rec = _RecordingTool()
        ctx = _make_builtin_ctx(_wrap_direct(rec), conversation_id=None)
        mock_ex = ctx.executor
        mock_ex._get_tool_header.return_value = "Memory"
        mock_ex._infer_syntax_hint.return_value = ""
        mock_ex._format_tool_result.return_value = "ok"
        await _run_builtin(ctx)
        assert rec.captured_kwargs.get("conversation_id") is None


# ===========================================================================
# 3. MemoryStorage.list_memories + get() — restored after diff deleted them.
# ===========================================================================

@pytest.fixture
def store(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


class TestListAndGetRestored:

    def test_get_returns_saved_memory(self, store):
        m = Memory(content="OBP RAM budget is 512MB", layer="architecture")
        store.save(m)
        got = store.get(m.id)
        assert got is not None
        assert got.content == "OBP RAM budget is 512MB"

    def test_get_missing_returns_none(self, store):
        assert store.get("m_nonexistent") is None

    def test_list_default_active_only(self, store):
        store.save(Memory(content="active one", status="active"))
        store.save(Memory(content="archived one", status="archived"))
        active = store.list_memories()
        assert len(active) == 1
        assert active[0].content == "active one"

    def test_list_filter_by_status(self, store):
        store.save(Memory(content="c1", status="contested"))
        store.save(Memory(content="a1", status="active"))
        contested = store.list_memories(status="contested")
        assert [m.content for m in contested] == ["c1"]

    def test_list_filter_by_layer(self, store):
        store.save(Memory(content="arch", layer="architecture"))
        store.save(Memory(content="dom", layer="domain_context"))
        arch = store.list_memories(layer="architecture")
        assert [m.content for m in arch] == ["arch"]

    def test_list_filter_by_tags(self, store):
        store.save(Memory(content="tagged", tags=["obp", "ram"]))
        store.save(Memory(content="other", tags=["fred"]))
        hits = store.list_memories(tags=["obp"])
        assert [m.content for m in hits] == ["tagged"]

    def test_list_tag_intersection_semantics(self, store):
        store.save(Memory(content="multi", tags=["a", "b"]))
        # Any-match: a memory with [a,b] matches a filter of [b,c]
        assert len(store.list_memories(tags=["b", "c"])) == 1
        assert len(store.list_memories(tags=["z"])) == 0


# ===========================================================================
# 4. MemoryStorage.save_many — single write, upsert semantics.
# ===========================================================================

class TestSaveMany:

    def test_empty_list_is_noop(self, store):
        store.save_many([])
        assert store.count()["total"] == 0

    def test_inserts_new_memories(self, store):
        mems = [Memory(content=f"fact {i}") for i in range(3)]
        store.save_many(mems)
        assert store.count()["total"] == 3

    def test_updates_existing_in_place(self, store):
        m = Memory(content="original")
        store.save(m)
        m.content = "updated"
        m.importance = 0.9
        store.save_many([m])
        got = store.get(m.id)
        assert got.content == "updated"
        assert got.importance == 0.9
        assert store.count()["total"] == 1  # no duplicate

    def test_mixed_insert_and_update(self, store):
        existing = Memory(content="keep")
        store.save(existing)
        existing.content = "keep-updated"
        new = Memory(content="brand new")
        store.save_many([existing, new])
        assert store.count()["total"] == 2
        assert store.get(existing.id).content == "keep-updated"
        assert store.get(new.id).content == "brand new"

    def test_single_file_write(self, store):
        """save_many must call _save_memories exactly once regardless of N."""
        mems = [Memory(content=f"f{i}") for i in range(5)]
        with patch.object(store, "_save_memories", wraps=store._save_memories) as spy:
            store.save_many(mems)
        assert spy.call_count == 1

    def test_save_many_vs_n_saves_write_count(self, store):
        """Contrast: N individual save() calls = N writes; save_many = 1."""
        mems = [Memory(content=f"f{i}") for i in range(4)]
        with patch.object(store, "_save_memories", wraps=store._save_memories) as spy:
            for m in mems:
                store.save(m)
        n_individual = spy.call_count
        store2_writes = 0
        store._memories_cache = None
        with patch.object(store, "_save_memories", wraps=store._save_memories) as spy2:
            store.save_many(mems)
        assert n_individual == 4
        assert spy2.call_count == 1


# ===========================================================================
# 5 & 6. MemorySearchTool — decay throttle + batched result-touch.
# ===========================================================================

class TestSearchToolDecayThrottle:

    def setup_method(self):
        import app.mcp.tools.memory_tools as mt
        mt._LAST_DECAY_SWEEP = 0.0

    @pytest.mark.asyncio
    async def test_decay_runs_first_call_then_throttled(self, tmp_path):
        import app.mcp.tools.memory_tools as mt
        from app.mcp.tools.memory_tools import MemorySearchTool

        st = MemoryStorage(memory_dir=tmp_path / "memory")
        # A stale, low-importance memory eligible for archival.
        old = Memory(content="stale fact about nothing", importance=0.4,
                     last_accessed="2000-01-01", status="active")
        st.save(old)
        fresh = Memory(content="obp ram budget fact", importance=0.4,
                       last_accessed=time.strftime("%Y-%m-%d"))
        st.save(fresh)

        tool = MemorySearchTool()
        with patch("app.storage.memory.get_memory_storage", return_value=st):
            # First call: decay sweep runs, archives the stale one.
            await tool.execute(query="obp", conversation_id="c1")
            assert st.get(old.id).status == "archived"

            # Re-activate it; second call within 10 min must NOT re-archive.
            reactivated = st.get(old.id)
            reactivated.status = "active"
            st.save(reactivated)
            await tool.execute(query="obp", conversation_id="c1")
            assert st.get(old.id).status == "active"  # throttle held

    @pytest.mark.asyncio
    async def test_decay_skip_does_not_swallow_results(self, tmp_path):
        """_SkipDecay must be caught without aborting the search."""
        import app.mcp.tools.memory_tools as mt
        from app.mcp.tools.memory_tools import MemorySearchTool
        mt._LAST_DECAY_SWEEP = time.time()  # force throttle to fire immediately

        st = MemoryStorage(memory_dir=tmp_path / "memory")
        st.save(Memory(content="obp ram budget is 512 mb", tags=["obp"]))
        tool = MemorySearchTool()
        with patch("app.storage.memory.get_memory_storage", return_value=st):
            result = await tool.execute(query="obp", conversation_id="c1")
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_result_touch_single_batched_write(self, tmp_path):
        from app.mcp.tools.memory_tools import MemorySearchTool
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        for i in range(5):
            st.save(Memory(content=f"obp fact number {i}", tags=["obp"]))
        tool = MemorySearchTool()
        with patch("app.storage.memory.get_memory_storage", return_value=st), \
             patch.object(st, "save_many", wraps=st.save_many) as spy:
            await tool.execute(query="obp", conversation_id="c1")
        # The result-touch loop should funnel through one save_many call
        # (decay sweep with no archivals does not call save_many).
        assert spy.call_count == 1

    @pytest.mark.asyncio
    async def test_importance_bumped_on_retrieval(self, tmp_path):
        from app.mcp.tools.memory_tools import MemorySearchTool
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="obp ram budget detail", tags=["obp"], importance=0.5)
        st.save(m)
        tool = MemorySearchTool()
        with patch("app.storage.memory.get_memory_storage", return_value=st):
            await tool.execute(query="obp", conversation_id="c1")
        assert st.get(m.id).importance > 0.5


# ===========================================================================
# 7. memory_feedback._prune_stale_state — leak bounds.
# ===========================================================================

class TestPruneStaleState:

    def setup_method(self):
        import app.memory.feedback as fb
        fb._loaded_per_conversation.clear()
        fb._labile_until.clear()

    def test_expired_labile_entries_dropped(self):
        import app.memory.feedback as fb
        now = int(time.time() * 1000)
        fb._labile_until["expired"] = now - 1000
        fb._labile_until["live"] = now + 3_600_000
        fb._prune_stale_state()
        assert "expired" not in fb._labile_until
        assert "live" in fb._labile_until

    def test_labile_capped_to_max(self):
        import app.memory.feedback as fb
        now = int(time.time() * 1000)
        # All live, but over the cap — keep the longest-lived ones.
        for i in range(fb._MAX_LABILE_ENTRIES + 50):
            fb._labile_until[f"m{i}"] = now + 1_000_000 + i
        fb._prune_stale_state()
        assert len(fb._labile_until) == fb._MAX_LABILE_ENTRIES
        # The highest-expiry entry must survive.
        survivor = f"m{fb._MAX_LABILE_ENTRIES + 49}"
        assert survivor in fb._labile_until

    def test_loaded_conversations_capped(self):
        import app.memory.feedback as fb
        for i in range(fb._MAX_TRACKED_CONVERSATIONS + 25):
            fb._loaded_per_conversation[f"conv{i}"].add("m1")
        fb._prune_stale_state()
        assert len(fb._loaded_per_conversation) == fb._MAX_TRACKED_CONVERSATIONS

    def test_prune_noop_when_under_caps(self):
        import app.memory.feedback as fb
        now = int(time.time() * 1000)
        fb._labile_until["a"] = now + 100000
        fb._loaded_per_conversation["c1"].add("m1")
        fb._prune_stale_state()
        assert fb._labile_until == {"a": now + 100000}
        assert fb._loaded_per_conversation["c1"] == {"m1"}

    def test_record_load_invokes_prune(self):
        import app.memory.feedback as fb
        now = int(time.time() * 1000)
        fb._labile_until["expired"] = now - 5000
        fb.record_load("c1", ["m_new"])
        # record_load calls _prune_stale_state, which drops the expired entry
        assert "expired" not in fb._labile_until


# ===========================================================================
# 8. apply_feedback — non-blocking (to_thread) + batched _apply_updates.
# ===========================================================================

class _FakeProvider:
    """Deterministic provider: returns the cached vector for a known text,
    else a fixed orthogonal vector."""
    def __init__(self, mapping):
        self._mapping = mapping

    def embed_text(self, text):
        # Response windows hash to a "used" vector for matching content.
        for needle, vec in self._mapping.items():
            if needle in text:
                return vec
        return np.array([0.0, 0.0, 1.0], dtype=np.float32)


class TestApplyFeedbackNonBlocking:

    def setup_method(self):
        import app.memory.feedback as fb
        fb._loaded_per_conversation.clear()
        fb._labile_until.clear()

    @pytest.mark.asyncio
    async def test_used_memory_bumps_counters_via_to_thread(self, tmp_path):
        import app.memory.feedback as fb
        from app.services.embedding_service import EmbeddingCache

        st = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="obp ram budget", retrieval_loaded_count=0)
        st.save(m)

        used_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        cache = EmbeddingCache(tmp_path / "emb", dim=3)
        cache.put(m.id, used_vec)

        provider = _FakeProvider({"obp budget answer": used_vec})
        fb.record_load("conv-1", [m.id])

        to_thread_calls = []
        real_to_thread = asyncio.to_thread

        async def _counting_to_thread(fn, *a, **k):
            to_thread_calls.append(fn)
            return await real_to_thread(fn, *a, **k)

        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=st), \
             patch("app.memory.feedback.asyncio.to_thread", side_effect=_counting_to_thread):
            res = await fb.apply_feedback("conv-1", "the obp budget answer is 512mb")

        # The embedding work went through asyncio.to_thread (non-blocking).
        assert len(to_thread_calls) == 1
        assert res["loaded"] == 1
        assert res["used"] == 1
        got = st.get(m.id)
        assert got.retrieval_loaded_count == 1
        assert got.retrieval_used_count == 1

    @pytest.mark.asyncio
    async def test_loaded_only_when_below_threshold(self, tmp_path):
        import app.memory.feedback as fb
        from app.services.embedding_service import EmbeddingCache

        st = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="unrelated fact")
        st.save(m)
        mem_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        cache = EmbeddingCache(tmp_path / "emb", dim=3)
        cache.put(m.id, mem_vec)
        # Provider returns orthogonal vector for everything (no match).
        provider = _FakeProvider({})
        fb.record_load("conv-2", [m.id])

        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=st):
            res = await fb.apply_feedback("conv-2", "completely different topic")

        got = st.get(m.id)
        assert res["used"] == 0
        assert got.retrieval_loaded_count == 1   # loaded bumped
        assert got.retrieval_used_count == 0     # not used

    @pytest.mark.asyncio
    async def test_apply_updates_single_batched_write(self, tmp_path):
        import app.memory.feedback as fb
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        ids = []
        for i in range(4):
            m = Memory(content=f"loaded fact {i}")
            st.save(m)
            ids.append(m.id)
        with patch("app.storage.memory.get_memory_storage", return_value=st), \
             patch.object(st, "save_many", wraps=st.save_many) as spy:
            fb._apply_updates(set(ids), used_ids=set())
        assert spy.call_count == 1
        for mid in ids:
            assert st.get(mid).retrieval_loaded_count == 1

    @pytest.mark.asyncio
    async def test_clears_conversation_after_apply(self, tmp_path):
        import app.memory.feedback as fb
        from app.services.embedding_service import EmbeddingCache
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="x")
        st.save(m)
        cache = EmbeddingCache(tmp_path / "emb", dim=3)
        cache.put(m.id, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        provider = _FakeProvider({})
        fb.record_load("conv-3", [m.id])
        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=st):
            await fb.apply_feedback("conv-3", "some response")
        assert fb.get_loaded_memory_ids("conv-3") == set()

    @pytest.mark.asyncio
    async def test_noop_provider_bumps_loaded_only(self, tmp_path):
        import app.memory.feedback as fb
        from app.services.embedding_service import NoopProvider
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="y")
        st.save(m)
        fb.record_load("conv-4", [m.id])
        with patch("app.services.embedding_service.get_embedding_provider", return_value=NoopProvider()), \
             patch("app.storage.memory.get_memory_storage", return_value=st):
            res = await fb.apply_feedback("conv-4", "resp")
        assert res["used"] == 0
        assert st.get(m.id).retrieval_loaded_count == 1


# ===========================================================================
# 9. Cold-cache semantic-search skip must be a *real* skip (else branch).
# ===========================================================================

class TestColdCacheSkip:

    @pytest.mark.asyncio
    async def test_cold_cache_does_not_call_semantic_search(self, tmp_path, monkeypatch):
        # Real provider so the embedding branch is attempted.
        monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        for i in range(4):
            st.save(Memory(content=f"obp fact {i}", tags=["obp"]))

        cold_cache = MagicMock()
        # Report >50% missing → cold path.
        cold_cache.missing_ids.return_value = ["x"] * 99

        called = {"semantic": False}

        def _fake_semantic(query, top_k=10):
            called["semantic"] = True
            return []

        with patch("app.services.embedding_service.get_embedding_cache", return_value=cold_cache), \
             patch("app.services.embedding_service.semantic_search", side_effect=_fake_semantic):
            results = st.search("obp", limit=5)

        # The else-branch fix means semantic_search is NOT called when cold.
        assert called["semantic"] is False
        # Keyword fallback still returns results.
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_warm_cache_calls_semantic_search(self, tmp_path):
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        for i in range(4):
            st.save(Memory(content=f"obp fact {i}", tags=["obp"]))

        warm_cache = MagicMock()
        warm_cache.missing_ids.return_value = []   # warm

        called = {"semantic": False}

        def _fake_semantic(query, top_k=10):
            called["semantic"] = True
            return []

        with patch("app.services.embedding_service.get_embedding_cache", return_value=warm_cache), \
             patch("app.services.embedding_service.semantic_search", side_effect=_fake_semantic):
            st.search("obp", limit=5)

        assert called["semantic"] is True


# ===========================================================================
# 10. /api/v1/memory/all must include contested memories.
# ===========================================================================

class TestApiAllIncludesContested:

    @pytest.mark.asyncio
    async def test_all_endpoint_returns_active_and_contested(self, tmp_path):
        from app.api.memory import list_all_memories
        st = MemoryStorage(memory_dir=tmp_path / "memory")
        st.save(Memory(content="active fact", status="active"))
        st.save(Memory(content="contested fact", status="contested"))
        st.save(Memory(content="archived fact", status="archived"))
        with patch("app.storage.memory.get_memory_storage", return_value=st):
            result = await list_all_memories()
        statuses = {m["status"] for m in result}
        contents = {m["content"] for m in result}
        assert "active" in statuses
        assert "contested" in statuses
        assert "archived" not in statuses   # archived stays hidden
        assert "active fact" in contents
        assert "contested fact" in contents
