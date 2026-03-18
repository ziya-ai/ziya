"""
Tests for legacy swarm recovery after server restart.

The root bug: rehydrate() skipped completed_partial plans entirely,
so the recovery API (retry, promote, cancel) returned 404 for any
swarm that wasn't launched in the current server session.

This test suite verifies:
1. completed_partial plans are loaded into in-memory state
2. Running plans are marked completed_partial AND loaded (not skipped)
3. Recovery operations work on rehydrated plans
4. Fully terminal plans (completed, cancelled) are still skipped
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
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(parents=True)
    contexts_dir = project_dir / "contexts"
    contexts_dir.mkdir(parents=True)
    return project_dir


def make_specs(*ids):
    return [
        DelegateSpec(
            delegate_id=did,
            name=f"Delegate {did}",
            emoji="🔵",
            scope=f"Task for {did}",
            files=[],
            dependencies=[],
        )
        for did in ids
    ]


def _build_mock_group(group_id, plan_dict):
    """Build a mock ChatGroup with taskPlan."""
    group = MagicMock()
    group.id = group_id
    group.taskPlan = plan_dict
    return group


def _build_mock_chat(chat_id, group_id, delegate_meta_dict):
    """Build a mock Chat with delegateMeta."""
    chat = MagicMock()
    chat.id = chat_id
    chat.delegateMeta = delegate_meta_dict
    # list() filtering uses groupId attribute
    chat.groupId = group_id
    return chat


# ---------------------------------------------------------------------------
# Test: completed_partial plans are rehydrated
# ---------------------------------------------------------------------------

class TestCompletedPartialRehydration:
    """Plans with status 'completed_partial' should be loaded into memory
    so the recovery API can operate on them."""

    def test_completed_partial_loaded_into_plans(self, tmp_project):
        mgr = DelegateManager("proj-1", tmp_project)
        group_id = "grp-partial-1"
        plan_id = "plan-partial-1"

        plan = TaskPlan(
            name="Partial Plan",
            description="Has failures",
            delegate_specs=make_specs("D1", "D2"),
            status="completed_partial",
            created_at=time.time() - 3600,
        )

        group = _build_mock_group(group_id, plan.model_dump())
        orch_chat = _build_mock_chat("orch-1", group_id, {
            "role": "orchestrator",
            "plan_id": plan_id,
            "status": "running",
        })
        d1_chat = _build_mock_chat("chat-d1", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D1",
            "status": "crystal",
            "crystal": MemoryCrystal(
                delegate_id="D1", task="D1", summary="Done",
                original_tokens=100, crystal_tokens=20, created_at=time.time(),
            ).model_dump(),
        })
        d2_chat = _build_mock_chat("chat-d2", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D2",
            "status": "failed",
        })

        # Mock storage
        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [orch_chat, d1_chat, d2_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()

        recovered = mgr.rehydrate()

        assert recovered == 1
        assert plan_id in mgr._plans
        assert group_id in mgr._group_to_plan
        assert mgr._group_to_plan[group_id] == plan_id
        assert mgr._statuses[plan_id]["D1"] == "crystal"
        assert mgr._statuses[plan_id]["D2"] == "failed"
        assert "D1" in mgr._crystals[plan_id]

    def test_completed_partial_accessible_via_api_lookup(self, tmp_project):
        """The group_to_plan mapping must be populated so APIs can find the plan."""
        mgr = DelegateManager("proj-1", tmp_project)
        group_id = "grp-api-test"
        plan_id = "plan-api-test"

        plan = TaskPlan(
            name="API Test Plan",
            description="",
            delegate_specs=make_specs("D1"),
            status="completed_partial",
            created_at=time.time(),
        )

        group = _build_mock_group(group_id, plan.model_dump())
        d1_chat = _build_mock_chat("chat-d1", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D1",
            "status": "interrupted",
        })

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [d1_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()

        mgr.rehydrate()

        # Simulate what the API does
        resolved_plan_id = mgr._group_to_plan.get(group_id)
        assert resolved_plan_id == plan_id, \
            "API would get 404 — _group_to_plan not populated for completed_partial"

        status = mgr.get_plan_status(plan_id)
        assert status is not None
        assert status["status"] == "completed_partial"
        assert "D1" in status["delegates"]


# ---------------------------------------------------------------------------
# Test: running plans at shutdown are loaded (not just marked and skipped)
# ---------------------------------------------------------------------------

class TestRunningPlanRehydration:
    """Plans that were 'running' at shutdown get marked completed_partial
    AND loaded into memory (previously they were marked + skipped)."""

    def test_running_plan_becomes_completed_partial_and_loaded(self, tmp_project):
        mgr = DelegateManager("proj-1", tmp_project)
        group_id = "grp-was-running"
        plan_id = "plan-was-running"

        plan = TaskPlan(
            name="Was Running",
            description="",
            delegate_specs=make_specs("D1", "D2"),
            status="running",  # was running at shutdown
            created_at=time.time() - 600,
        )

        group = _build_mock_group(group_id, plan.model_dump())
        d1_chat = _build_mock_chat("chat-d1", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D1",
            "status": "running",  # was mid-flight
        })
        d2_chat = _build_mock_chat("chat-d2", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D2",
            "status": "crystal",
            "crystal": MemoryCrystal(
                delegate_id="D2", task="D2", summary="Done",
                original_tokens=50, crystal_tokens=10, created_at=time.time(),
            ).model_dump(),
        })

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [d1_chat, d2_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()

        recovered = mgr.rehydrate()

        # Plan should be in memory
        assert recovered == 1
        assert plan_id in mgr._plans
        assert mgr._plans[plan_id].status == "completed_partial"

        # Group mapping must exist
        assert mgr._group_to_plan[group_id] == plan_id

        # D1 was running → should be interrupted
        assert mgr._statuses[plan_id]["D1"] == "interrupted"
        # D2 was crystal → stays crystal
        assert mgr._statuses[plan_id]["D2"] == "crystal"

    def test_running_plan_persists_status_change(self, tmp_project):
        """The completed_partial status should be written to disk."""
        mgr = DelegateManager("proj-1", tmp_project)

        plan = TaskPlan(
            name="Persist Test",
            description="",
            delegate_specs=make_specs("D1"),
            status="running",
            created_at=time.time(),
        )
        group = _build_mock_group("grp-persist", plan.model_dump())
        d1_chat = _build_mock_chat("chat-d1", "grp-persist", {
            "role": "delegate",
            "plan_id": "plan-persist",
            "delegate_id": "D1",
            "status": "running",
        })

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [d1_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()

        mgr.rehydrate()

        # Verify _patch_group_task_plan was called with completed_partial
        mgr._patch_group_task_plan.assert_called_once()
        call_args = mgr._patch_group_task_plan.call_args
        assert call_args[0][0] == "grp-persist"
        persisted_plan = call_args[0][1]
        assert persisted_plan.status == "completed_partial"


# ---------------------------------------------------------------------------
# Test: fully terminal plans are still skipped
# ---------------------------------------------------------------------------

class TestTerminalPlanSkipped:
    """completed and cancelled plans should NOT be loaded."""

    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    def test_terminal_plan_not_loaded(self, tmp_project, status):
        mgr = DelegateManager("proj-1", tmp_project)

        plan = TaskPlan(
            name="Terminal Plan",
            description="",
            delegate_specs=make_specs("D1"),
            status=status,
            created_at=time.time(),
        )
        group = _build_mock_group("grp-terminal", plan.model_dump())

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = []
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)

        recovered = mgr.rehydrate()

        assert recovered == 0
        assert len(mgr._plans) == 0
        assert "grp-terminal" not in mgr._group_to_plan


# ---------------------------------------------------------------------------
# Test: recovery operations work on rehydrated plans
# ---------------------------------------------------------------------------

class TestRecoveryOnRehydratedPlan:
    """After rehydration, retry/promote should work just like on live plans."""

    @pytest.mark.asyncio
    async def test_retry_on_rehydrated_plan(self, tmp_project):
        mgr = DelegateManager("proj-1", tmp_project)
        group_id = "grp-retry"
        plan_id = "plan-retry"

        plan = TaskPlan(
            name="Retry Test",
            description="",
            delegate_specs=make_specs("D1"),
            status="completed_partial",
            created_at=time.time(),
        )
        plan.delegate_specs[0].conversation_id = "chat-d1"

        group = _build_mock_group(group_id, plan.model_dump())
        d1_chat = _build_mock_chat("chat-d1", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D1",
            "delegate_spec": plan.delegate_specs[0].model_dump(),
            "status": "failed",
        })

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [d1_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()
        mgr._persist_plan = MagicMock()

        mgr.rehydrate()

        # Mock _resolve_and_start to avoid launching actual streams
        mgr._resolve_and_start = AsyncMock()

        result = await mgr.retry_delegate(plan_id, "D1")

        assert result["retried"] == "D1"
        assert mgr._statuses[plan_id]["D1"] == "proposed"
        # Plan should transition back to running
        assert mgr._plans[plan_id].status == "running"

    @pytest.mark.asyncio
    async def test_promote_on_rehydrated_plan(self, tmp_project):
        mgr = DelegateManager("proj-1", tmp_project)
        group_id = "grp-promote"
        plan_id = "plan-promote"

        plan = TaskPlan(
            name="Promote Test",
            description="",
            delegate_specs=make_specs("D1"),
            status="completed_partial",
            created_at=time.time(),
        )
        plan.delegate_specs[0].conversation_id = "chat-d1"

        group = _build_mock_group(group_id, plan.model_dump())
        d1_chat = _build_mock_chat("chat-d1", group_id, {
            "role": "delegate",
            "plan_id": plan_id,
            "delegate_id": "D1",
            "delegate_spec": plan.delegate_specs[0].model_dump(),
            "status": "interrupted",
        })

        mock_gs = MagicMock()
        mock_gs.list.return_value = [group]
        mock_cs = MagicMock()
        mock_cs.list.return_value = [d1_chat]
        mgr._get_group_storage = MagicMock(return_value=mock_gs)
        mgr._get_chat_storage = MagicMock(return_value=mock_cs)
        mgr._patch_group_task_plan = MagicMock()
        mgr._patch_chat_status = MagicMock()
        mgr._patch_chat_crystal = MagicMock()
        mgr._persist_plan = MagicMock()
        mgr._persist_delegate_message = MagicMock()
        mgr._orchestrator_receive_crystal = AsyncMock()
        mgr._orchestrator_final_synthesis = AsyncMock(return_value="")
        mgr._post_completion_to_source = MagicMock()

        mgr.rehydrate()

        result = await mgr.promote_to_stub_crystal(plan_id, "D1")

        assert result["promoted"] == "D1"
        assert mgr._statuses[plan_id]["D1"] == "crystal"
        assert "D1" in mgr._crystals[plan_id]
