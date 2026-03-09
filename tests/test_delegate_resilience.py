"""
Tests for delegate resilience features added in the swarm stability pass:

- Progressive checkpointing (content-volume based snapshots)
- Self-rescue on stream failure (continuation delegates)
- Stall watchdog (sub-plan aware silence detection)
- Pending subplan blocking (_is_plan_complete waits for children)
- Subplan completion triggers parent finalization
- Orchestrator direct LLM calls (not routed through compaction)
- Synthesis included in source conversation rollup
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.models.delegate import (
    DelegateSpec, TaskPlan, MemoryCrystal, SwarmTask,
)
from app.agents.delegate_manager import DelegateManager, reset_delegate_manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset():
    reset_delegate_manager()
    yield
    reset_delegate_manager()


@pytest.fixture
def manager(tmp_path):
    project_dir = tmp_path / "test-project"
    (project_dir / "chats").mkdir(parents=True)
    (project_dir / "contexts").mkdir(parents=True)
    mgr = DelegateManager("test-project", project_dir, max_concurrency=2)
    mgr._persist_plan = MagicMock()
    mgr._patch_chat_delegate_meta = MagicMock()
    mgr._patch_chat_crystal = MagicMock()
    mgr._patch_chat_status = MagicMock()
    mgr._patch_group_task_plan = MagicMock()
    mgr._persist_delegate_message = MagicMock()
    return mgr


def make_spec(did, name="task", deps=None, conv_id=None):
    return DelegateSpec(
        delegate_id=did, name=name, scope=f"Do {name}",
        dependencies=deps or [], conversation_id=conv_id or f"conv-{did}",
    )


def make_crystal(did, summary="Done"):
    return MemoryCrystal(
        delegate_id=did, task=f"task-{did}", summary=summary,
        original_tokens=5000, crystal_tokens=200, created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Progressive Checkpointing
# ---------------------------------------------------------------------------

class TestProgressiveCheckpointing:

    def test_persist_checkpoint_stores_data(self, manager):
        manager._persist_checkpoint("p1", "D1", "a" * 4000, [{"chars": 4000}])
        cp = manager._get_checkpoint("p1", "D1")
        assert cp is not None
        assert cp["accumulated"] == "a" * 4000
        assert len(cp["checkpoints"]) == 1

    def test_get_checkpoint_returns_none_for_unknown(self, manager):
        assert manager._get_checkpoint("p1", "D1") is None

    def test_multiple_checkpoints_overwrite(self, manager):
        manager._persist_checkpoint("p1", "D1", "a" * 4000, [{"chars": 4000}])
        manager._persist_checkpoint("p1", "D1", "a" * 8000, [
            {"chars": 4000}, {"chars": 8000}
        ])
        cp = manager._get_checkpoint("p1", "D1")
        assert len(cp["checkpoints"]) == 2
        assert len(cp["accumulated"]) == 8000

    def test_checkpoints_are_delegate_scoped(self, manager):
        manager._persist_checkpoint("p1", "D1", "first", [{"chars": 5}])
        manager._persist_checkpoint("p1", "D2", "second", [{"chars": 6}])
        assert manager._get_checkpoint("p1", "D1")["accumulated"] == "first"
        assert manager._get_checkpoint("p1", "D2")["accumulated"] == "second"


# ---------------------------------------------------------------------------
# Sub-plan Awareness
# ---------------------------------------------------------------------------

class TestHasActiveSubplans:

    def test_no_subplans(self, manager):
        manager._plans["p1"] = TaskPlan(name="Parent")
        assert manager._has_active_subplans("p1", "D1") is False

    def test_running_subplan_detected(self, manager):
        manager._plans["p1"] = TaskPlan(name="Parent")
        manager._plans["sub1"] = TaskPlan(
            name="Child", parent_plan_id="p1",
            parent_delegate_id="D1", status="running",
        )
        assert manager._has_active_subplans("p1", "D1") is True

    def test_completed_subplan_not_active(self, manager):
        manager._plans["p1"] = TaskPlan(name="Parent")
        manager._plans["sub1"] = TaskPlan(
            name="Child", parent_plan_id="p1",
            parent_delegate_id="D1", status="completed",
        )
        assert manager._has_active_subplans("p1", "D1") is False

    def test_other_delegates_subplan_ignored(self, manager):
        manager._plans["p1"] = TaskPlan(name="Parent")
        manager._plans["sub1"] = TaskPlan(
            name="Child", parent_plan_id="p1",
            parent_delegate_id="D2", status="running",
        )
        assert manager._has_active_subplans("p1", "D1") is False

    def test_failed_subplan_not_active(self, manager):
        manager._plans["p1"] = TaskPlan(name="Parent")
        manager._plans["sub1"] = TaskPlan(
            name="Child", parent_plan_id="p1",
            parent_delegate_id="D1", status="failed",
        )
        assert manager._has_active_subplans("p1", "D1") is False


# ---------------------------------------------------------------------------
# Pending Subplan Blocking
# ---------------------------------------------------------------------------

class TestPendingSubplanBlocking:

    def test_plan_not_complete_with_pending_subplans(self, manager):
        plan = TaskPlan(name="Parent")
        plan.pending_subplan_ids = {"sub1"}
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "crystal"}
        assert manager._is_plan_complete("p1") is False

    def test_plan_complete_after_subplan_cleared(self, manager):
        plan = TaskPlan(name="Parent")
        plan.pending_subplan_ids = set()
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "crystal"}
        assert manager._is_plan_complete("p1") is True

    def test_plan_without_pending_attr_is_normal(self, manager):
        """Plans created before the feature should still work."""
        plan = TaskPlan(name="Legacy")
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "crystal", "D2": "failed"}
        assert manager._is_plan_complete("p1") is True


# ---------------------------------------------------------------------------
# Self-Rescue
# ---------------------------------------------------------------------------

class TestSelfRescue:

    @pytest.mark.asyncio
    async def test_rescue_launches_continuation(self, manager):
        plan = TaskPlan(name="Test", delegate_specs=[make_spec("D1")])
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}
        manager._running["p1"] = {"D1"}
        spec = plan.delegate_specs[0]

        accumulated = "x" * 500
        checkpoints = [{"chars": 500, "ts": time.time()}]
        manager._persist_checkpoint("p1", "D1", accumulated, checkpoints)

        with patch("asyncio.create_task") as mock_ct:
            result = await manager._attempt_rescue("p1", spec, accumulated, checkpoints)

        assert result is True
        mock_ct.assert_called_once()

    @pytest.mark.asyncio
    async def test_rescue_skipped_with_active_subplans(self, manager):
        plan = TaskPlan(name="Test", delegate_specs=[make_spec("D1")])
        manager._plans["p1"] = plan
        manager._plans["sub1"] = TaskPlan(
            name="Sub", parent_plan_id="p1",
            parent_delegate_id="D1", status="running",
        )

        spec = plan.delegate_specs[0]
        result = await manager._attempt_rescue("p1", spec, "x" * 500, [])
        assert result is False

    @pytest.mark.asyncio
    async def test_rescue_skipped_insufficient_work(self, manager):
        plan = TaskPlan(name="Test", delegate_specs=[make_spec("D1")])
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}

        spec = plan.delegate_specs[0]
        result = await manager._attempt_rescue("p1", spec, "tiny", [])
        assert result is False

    @pytest.mark.asyncio
    async def test_rescue_only_once(self, manager):
        plan = TaskPlan(name="Test", delegate_specs=[make_spec("D1")])
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}
        manager._running["p1"] = {"D1"}
        spec = plan.delegate_specs[0]
        accumulated = "x" * 500

        with patch("asyncio.create_task"):
            r1 = await manager._attempt_rescue("p1", spec, accumulated, [])
            r2 = await manager._attempt_rescue("p1", spec, accumulated, [])

        assert r1 is True
        assert r2 is False


# ---------------------------------------------------------------------------
# Stall Watchdog
# ---------------------------------------------------------------------------

class TestStallWatchdog:

    def test_stall_detected_after_silence(self, manager):
        plan = TaskPlan(
            name="Test", delegate_specs=[make_spec("D1")],
            status="running", created_at=time.time() - 700,
        )
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}
        manager._crystals["p1"] = {}
        manager._running["p1"] = {"D1"}

        status = manager.get_delegate_status("p1")
        assert status is not None
        assert status["delegates"]["D1"]["status"] == "stalled"

    def test_no_stall_with_active_subplans(self, manager):
        plan = TaskPlan(
            name="Test", delegate_specs=[make_spec("D1")],
            status="running", created_at=time.time() - 700,
        )
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}
        manager._crystals["p1"] = {}
        manager._running["p1"] = {"D1"}
        manager._plans["sub1"] = TaskPlan(
            name="Sub", parent_plan_id="p1",
            parent_delegate_id="D1", status="running",
        )

        status = manager.get_delegate_status("p1")
        assert status["delegates"]["D1"]["status"] == "running"

    def test_no_stall_with_recent_checkpoint(self, manager):
        plan = TaskPlan(
            name="Test", delegate_specs=[make_spec("D1")],
            status="running", created_at=time.time() - 700,
        )
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "running"}
        manager._crystals["p1"] = {}
        manager._running["p1"] = {"D1"}
        manager._persist_checkpoint("p1", "D1", "x" * 4000, [
            {"chars": 4000, "ts": time.time()}
        ])

        status = manager.get_delegate_status("p1")
        assert status["delegates"]["D1"]["status"] == "running"


# ---------------------------------------------------------------------------
# Source Conversation Rollup with Synthesis
# ---------------------------------------------------------------------------

class TestSourceRollupWithSynthesis:

    def test_synthesis_included_in_source_message(self, manager):
        plan = TaskPlan(
            name="Test", delegate_specs=[make_spec("D1")],
            status="completed", created_at=time.time(),
            source_conversation_id="src1",
        )
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "crystal"}
        manager._crystals["p1"] = {"D1": make_crystal("D1")}

        written = []
        mock_cs = MagicMock()
        mock_cs.add_message = MagicMock(
            side_effect=lambda cid, msg: written.append((cid, msg))
        )
        manager._get_chat_storage = MagicMock(return_value=mock_cs)

        manager._post_completion_to_source("p1", synthesis="All tasks succeeded with full coverage.")

        assert len(written) == 1
        content = written[0][1].content
        assert "Orchestrator Synthesis" in content
        assert "All tasks succeeded" in content

    def test_no_synthesis_omits_section(self, manager):
        plan = TaskPlan(
            name="Test", delegate_specs=[make_spec("D1")],
            status="completed", created_at=time.time(),
            source_conversation_id="src1",
        )
        manager._plans["p1"] = plan
        manager._statuses["p1"] = {"D1": "crystal"}
        manager._crystals["p1"] = {"D1": make_crystal("D1")}

        written = []
        mock_cs = MagicMock()
        mock_cs.add_message = MagicMock(
            side_effect=lambda cid, msg: written.append((cid, msg))
        )
        manager._get_chat_storage = MagicMock(return_value=mock_cs)

        manager._post_completion_to_source("p1")

        content = written[0][1].content
        assert "Orchestrator Synthesis" not in content


# ---------------------------------------------------------------------------
# Orchestrator LLM Call (direct, not through compaction)
# ---------------------------------------------------------------------------

class TestOrchestratorLLMCall:

    @pytest.mark.asyncio
    async def test_direct_llm_call_not_compaction(self, manager):
        """Orchestrator should call the model directly, not CompactionEngine."""
        with patch("app.agents.delegate_manager.lazy_model") as mock_lazy:
            mock_model = AsyncMock()
            mock_model.ainvoke.return_value = MagicMock(content="Analysis result here")
            mock_wrapper = MagicMock()
            mock_wrapper.model = mock_model
            mock_lazy.get_model.return_value = mock_wrapper

            result = await manager._orchestrator_llm_call("Analyze this plan")

        assert result == "Analysis result here"
        mock_model.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_call_truncates_at_4000(self, manager):
        with patch("app.agents.delegate_manager.lazy_model") as mock_lazy:
            mock_model = AsyncMock()
            mock_model.ainvoke.return_value = MagicMock(content="x" * 5000)
            mock_wrapper = MagicMock()
            mock_wrapper.model = mock_model
            mock_lazy.get_model.return_value = mock_wrapper

            result = await manager._orchestrator_llm_call("prompt")

        assert len(result) == 4000
