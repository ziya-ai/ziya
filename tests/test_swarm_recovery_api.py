"""
Tests for the swarm recovery API endpoints (retry, promote-stub, cancel)
and their integration with DelegateManager state transitions.
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from app.models.delegate import (
    DelegateSpec, DelegateMeta, TaskPlan, MemoryCrystal, SwarmTask,
)
from app.agents.delegate_manager import DelegateManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    project_dir = tmp_path / "test-project"
    (project_dir / "chats").mkdir(parents=True)
    (project_dir / "contexts").mkdir(parents=True)
    return project_dir


@pytest.fixture
def manager(tmp_project):
    mgr = DelegateManager("proj-1", tmp_project, max_concurrency=2)
    mgr._persist_plan = MagicMock()
    mgr._patch_chat_delegate_meta = MagicMock()
    mgr._patch_chat_crystal = MagicMock()
    mgr._patch_chat_status = MagicMock()
    mgr._patch_group_task_plan = MagicMock()
    mgr._persist_delegate_message = MagicMock()
    return mgr


def make_specs(*ids):
    return [
        DelegateSpec(
            delegate_id=did,
            name=f"Delegate {did}",
            scope=f"Work on {did}",
            files=[],
            dependencies=[],
            emoji="🔵",
        )
        for did in ids
    ]


def setup_plan(manager, specs, statuses=None):
    """Create an in-memory plan with given specs and statuses."""
    plan_id = "plan-test-001"
    plan = TaskPlan(
        name="Test Plan",
        description="A test plan",
        delegate_specs=specs,
        status="running",
        created_at=time.time(),
        task_list=[
            SwarmTask(task_id=f"st_{s.delegate_id}", title=s.name, status="open", created_at=time.time())
            for s in specs
        ],
    )

    manager._plans[plan_id] = plan
    manager._statuses[plan_id] = {
        s.delegate_id: (statuses or {}).get(s.delegate_id, "proposed")
        for s in specs
    }
    manager._crystals[plan_id] = {}
    manager._running[plan_id] = set()
    manager._tasks[plan_id] = {}
    manager._callbacks[plan_id] = None
    manager._group_to_plan["group-001"] = plan_id

    return plan_id


# ---------------------------------------------------------------------------
# Retry delegate
# ---------------------------------------------------------------------------

class TestRetryDelegate:

    @pytest.mark.asyncio
    async def test_retry_failed_delegate_resets_to_proposed(self, manager):
        specs = make_specs("D1", "D2")
        plan_id = setup_plan(manager, specs, {"D1": "failed", "D2": "proposed"})

        # Mock _resolve_and_start so it doesn't actually launch anything
        manager._resolve_and_start = AsyncMock()

        result = await manager.retry_delegate(plan_id, "D1")
        assert result["retried"] == "D1"
        assert manager._statuses[plan_id]["D1"] == "proposed"
        manager._resolve_and_start.assert_called_once_with(plan_id)

    @pytest.mark.asyncio
    async def test_retry_interrupted_delegate(self, manager):
        specs = make_specs("D1")
        plan_id = setup_plan(manager, specs, {"D1": "interrupted"})
        manager._resolve_and_start = AsyncMock()

        result = await manager.retry_delegate(plan_id, "D1")
        assert result["retried"] == "D1"
        assert manager._statuses[plan_id]["D1"] == "proposed"

    @pytest.mark.asyncio
    async def test_retry_non_failed_raises(self, manager):
        specs = make_specs("D1")
        plan_id = setup_plan(manager, specs, {"D1": "running"})

        with pytest.raises(ValueError, match="not retryable"):
            await manager.retry_delegate(plan_id, "D1")

    @pytest.mark.asyncio
    async def test_retry_unknown_plan_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown plan"):
            await manager.retry_delegate("nonexistent", "D1")

    @pytest.mark.asyncio
    async def test_retry_cascades_downstream_reset(self, manager):
        """When D1 fails, D2 (which depends on D1) should also be reset."""
        specs = [
            DelegateSpec(delegate_id="D1", name="D1", scope="x", files=[], dependencies=[], emoji="🔵"),
            DelegateSpec(delegate_id="D2", name="D2", scope="x", files=[], dependencies=["D1"], emoji="🔵"),
        ]
        plan_id = setup_plan(manager, specs, {"D1": "failed", "D2": "failed"})
        manager._resolve_and_start = AsyncMock()

        await manager.retry_delegate(plan_id, "D1")
        assert manager._statuses[plan_id]["D1"] == "proposed"
        assert manager._statuses[plan_id]["D2"] == "proposed"


# ---------------------------------------------------------------------------
# Promote to stub crystal
# ---------------------------------------------------------------------------

class TestPromoteToStub:

    @pytest.mark.asyncio
    async def test_promote_creates_stub_crystal(self, manager):
        specs = make_specs("D1")
        plan_id = setup_plan(manager, specs, {"D1": "failed"})

        # Mock on_crystal_ready to prevent full cascade (it needs storage)
        manager.on_crystal_ready = AsyncMock()

        result = await manager.promote_to_stub_crystal(plan_id, "D1")
        assert result["promoted"] == "D1"
        assert result["status"] == "crystal"

        # Verify on_crystal_ready was called with a stub crystal
        manager.on_crystal_ready.assert_called_once()
        crystal_arg = manager.on_crystal_ready.call_args[0][2]
        assert isinstance(crystal_arg, MemoryCrystal)
        assert "stub crystal" in crystal_arg.summary.lower()

    @pytest.mark.asyncio
    async def test_promote_non_failed_raises(self, manager):
        specs = make_specs("D1")
        plan_id = setup_plan(manager, specs, {"D1": "crystal"})

        with pytest.raises(ValueError, match="must be 'failed' or 'interrupted'"):
            await manager.promote_to_stub_crystal(plan_id, "D1")


# ---------------------------------------------------------------------------
# Cancel all
# ---------------------------------------------------------------------------

class TestCancelPlan:

    @pytest.mark.asyncio
    async def test_cancel_stops_running_delegates(self, manager):
        specs = make_specs("D1", "D2")
        plan_id = setup_plan(manager, specs, {"D1": "running", "D2": "running"})

        # Create mock tasks
        mock_task1 = MagicMock()
        mock_task1.done.return_value = False
        mock_task2 = MagicMock()
        mock_task2.done.return_value = False
        manager._tasks[plan_id] = {"D1": mock_task1, "D2": mock_task2}
        manager._running[plan_id] = {"D1", "D2"}

        manager._emit = AsyncMock()

        await manager.cancel_plan(plan_id)
        mock_task1.cancel.assert_called_once()
        mock_task2.cancel.assert_called_once()
        assert manager._plans[plan_id].status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_preserves_crystals(self, manager):
        """Cancelling should not destroy already-completed crystals."""
        specs = make_specs("D1", "D2")
        plan_id = setup_plan(manager, specs, {"D1": "crystal", "D2": "running"})

        crystal = MemoryCrystal(
            delegate_id="D1", task="D1", summary="Done",
            original_tokens=100, crystal_tokens=50, created_at=time.time(),
        )
        manager._crystals[plan_id]["D1"] = crystal

        mock_task = MagicMock()
        mock_task.done.return_value = False
        manager._tasks[plan_id] = {"D2": mock_task}
        manager._running[plan_id] = {"D2"}
        manager._emit = AsyncMock()

        await manager.cancel_plan(plan_id)

        # Crystal should still be there
        assert "D1" in manager._crystals[plan_id]
        assert manager._crystals[plan_id]["D1"].summary == "Done"


# ---------------------------------------------------------------------------
# Plan status reporting with needs_attention
# ---------------------------------------------------------------------------

class TestPlanStatusReporting:

    def test_needs_attention_lists_failed(self, manager):
        specs = make_specs("D1", "D2", "D3")
        plan_id = setup_plan(manager, specs, {
            "D1": "crystal", "D2": "failed", "D3": "interrupted"
        })

        status = manager.get_plan_status(plan_id)
        assert status is not None
        assert "D2" in status["needs_attention"]
        assert "D3" in status["needs_attention"]
        assert "D1" not in status["needs_attention"]

    def test_needs_attention_empty_when_healthy(self, manager):
        specs = make_specs("D1", "D2")
        plan_id = setup_plan(manager, specs, {"D1": "crystal", "D2": "running"})

        status = manager.get_plan_status(plan_id)
        assert status["needs_attention"] == []


# ---------------------------------------------------------------------------
# Completed_partial plan detection
# ---------------------------------------------------------------------------

class TestCompletedPartialPlan:

    @pytest.mark.asyncio
    async def test_plan_completes_partial_on_mixed_results(self, manager):
        """A plan with both crystals and failures should end as completed_partial."""
        specs = make_specs("D1", "D2")
        plan_id = setup_plan(manager, specs, {"D1": "crystal", "D2": "failed"})
        plan = manager._plans[plan_id]
        plan.crystals = []  # will be populated by on_crystal_ready

        # Both are terminal, so _is_plan_complete should return True
        assert manager._is_plan_complete(plan_id)

        # Verify statuses
        statuses = manager._statuses[plan_id]
        has_failures = any(s == "failed" for s in statuses.values())
        expected_status = "completed_partial" if has_failures else "completed"
        assert expected_status == "completed_partial"
