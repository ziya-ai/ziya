"""
Tests for recursive swarm support — delegates spawning sub-plans.

Covers:
- TaskPlan parent linkage fields
- launch_subplan method
- Crystal rollup from sub-plan to parent plan
- SwarmLaunchSubplanTool
- Multi-level nesting
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.models.delegate import (
    TaskPlan, DelegateSpec, MemoryCrystal, DelegateMeta,
    SwarmTask, FileChange,
)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestTaskPlanParentLinkage:

    def test_parent_fields_default_none(self):
        plan = TaskPlan(name="test")
        assert plan.parent_plan_id is None
        assert plan.parent_delegate_id is None

    def test_parent_fields_set(self):
        plan = TaskPlan(
            name="sub-plan",
            parent_plan_id="plan-123",
            parent_delegate_id="D2",
        )
        assert plan.parent_plan_id == "plan-123"
        assert plan.parent_delegate_id == "D2"

    def test_parent_fields_roundtrip(self):
        plan = TaskPlan(
            name="sub",
            parent_plan_id="p1",
            parent_delegate_id="D1",
        )
        d = plan.model_dump()
        restored = TaskPlan(**d)
        assert restored.parent_plan_id == "p1"
        assert restored.parent_delegate_id == "D1"

    def test_parent_fields_json_roundtrip(self):
        plan = TaskPlan(
            name="sub",
            parent_plan_id="p1",
            parent_delegate_id="D1",
        )
        j = plan.model_dump(mode="json")
        assert j["parent_plan_id"] == "p1"
        restored = TaskPlan(**j)
        assert restored.parent_plan_id == "p1"


# ---------------------------------------------------------------------------
# DelegateManager recursive tests (mocked storage)
# ---------------------------------------------------------------------------

def _make_mock_manager():
    """Create a DelegateManager with mocked storage."""
    with patch("app.agents.delegate_manager.DelegateManager.__init__", return_value=None):
        from app.agents.delegate_manager import DelegateManager
        mgr = DelegateManager.__new__(DelegateManager)

    mgr.project_id = "test-project"
    mgr.project_dir = "/tmp/test"
    mgr.max_concurrency = 4
    mgr._plans = {}
    mgr._statuses = {}
    mgr._crystals = {}
    mgr._running = {}
    mgr._tasks = {}
    mgr._callbacks = {}
    mgr._group_to_plan = {}
    mgr._semaphore = asyncio.Semaphore(4)

    import threading
    mgr._persist_lock = threading.RLock()

    # Mock storage
    mock_group_storage = MagicMock()
    mock_chat_storage = MagicMock()
    mock_context_storage = MagicMock()

    mgr._get_group_storage = MagicMock(return_value=mock_group_storage)
    mgr._get_chat_storage = MagicMock(return_value=mock_chat_storage)
    mgr._get_context_storage = MagicMock(return_value=mock_context_storage)
    mgr._persist_plan = MagicMock()
    mgr._persist_delegate_message = MagicMock()
    mgr._patch_group_task_plan = MagicMock()
    mgr._patch_chat_delegate_meta = MagicMock()
    mgr._emit = AsyncMock()

    # Make create calls return mock objects with IDs
    mock_group = MagicMock()
    mock_group.id = "group-parent"
    mock_group_storage.create.return_value = mock_group

    chat_counter = {"n": 0}
    def make_chat(*a, **kw):
        chat_counter["n"] += 1
        c = MagicMock()
        c.id = f"chat-{chat_counter['n']}"
        return c
    mock_chat_storage.create.side_effect = make_chat

    ctx_counter = {"n": 0}
    def make_ctx(*a, **kw):
        ctx_counter["n"] += 1
        c = MagicMock()
        c.id = f"ctx-{ctx_counter['n']}"
        return c
    mock_context_storage.create.side_effect = make_ctx

    return mgr


class TestLaunchSubplan:

    @pytest.mark.asyncio
    async def test_launch_subplan_sets_parent_linkage(self):
        mgr = _make_mock_manager()

        # Setup parent plan
        parent_specs = [DelegateSpec(delegate_id="D1", name="Task 1")]
        result = await mgr.launch_plan("Parent", "desc", parent_specs)
        parent_plan_id = result["plan_id"]

        # Reset mock counters
        mgr._get_group_storage().create.return_value = MagicMock(id="group-child")

        # Launch sub-plan
        sub_specs = [DelegateSpec(delegate_id="D1", name="Sub task 1")]
        sub_result = await mgr.launch_subplan(
            name="Child Plan",
            description="sub work",
            delegate_specs=sub_specs,
            source_conversation_id="chat-1",
            parent_plan_id=parent_plan_id,
            parent_delegate_id="D1",
        )

        sub_plan_id = sub_result["plan_id"]
        sub_plan = mgr._plans[sub_plan_id]
        assert sub_plan.parent_plan_id == parent_plan_id
        assert sub_plan.parent_delegate_id == "D1"

    @pytest.mark.asyncio
    async def test_subplan_is_tracked_independently(self):
        mgr = _make_mock_manager()

        parent_specs = [DelegateSpec(delegate_id="D1", name="Task 1")]
        result = await mgr.launch_plan("Parent", "desc", parent_specs)
        parent_id = result["plan_id"]

        mgr._get_group_storage().create.return_value = MagicMock(id="group-child")
        sub_specs = [DelegateSpec(delegate_id="D1", name="Sub 1")]
        sub_result = await mgr.launch_subplan(
            name="Child", description="", delegate_specs=sub_specs,
            parent_plan_id=parent_id, parent_delegate_id="D1",
        )

        # Both plans tracked
        assert parent_id in mgr._plans
        assert sub_result["plan_id"] in mgr._plans
        assert len(mgr._plans) == 2


class TestCrystalRollup:

    @pytest.mark.asyncio
    async def test_subplan_completion_adds_to_parent_task_list(self):
        mgr = _make_mock_manager()

        # Setup parent
        parent_specs = [DelegateSpec(delegate_id="D1", name="Parent Task")]
        result = await mgr.launch_plan("Parent", "desc", parent_specs)
        parent_id = result["plan_id"]
        parent_plan = mgr._plans[parent_id]
        parent_plan.orchestrator_id = "orch-1"
        initial_task_count = len(parent_plan.task_list)

        # Setup sub-plan
        mgr._get_group_storage().create.return_value = MagicMock(id="group-sub")
        sub_specs = [DelegateSpec(delegate_id="D1", name="Sub Task")]
        sub_result = await mgr.launch_subplan(
            name="SubPlan", description="", delegate_specs=sub_specs,
            parent_plan_id=parent_id, parent_delegate_id="D1",
        )
        sub_id = sub_result["plan_id"]

        # Simulate sub-plan delegate completing with a crystal
        crystal = MemoryCrystal(
            delegate_id="D1", task="Sub Task",
            summary="Did the sub-work",
            files_changed=[FileChange(path="sub.py", action="created")],
        )
        mgr._crystals[sub_id] = {"D1": crystal}
        mgr._statuses[sub_id]["D1"] = "crystal"

        # Trigger completion
        sub_plan = mgr._plans[sub_id]
        sub_plan.status = "completed"
        sub_plan.completed_at = time.time()
        await mgr._on_subplan_complete(sub_id)

        # Parent task list should have a new entry
        assert len(parent_plan.task_list) == initial_task_count + 1
        subplan_task = parent_plan.task_list[-1]
        assert subplan_task.status == "done"
        assert "subplan" in subplan_task.tags
        assert "SubPlan" in subplan_task.title

    @pytest.mark.asyncio
    async def test_subplan_notifies_parent_orchestrator(self):
        mgr = _make_mock_manager()

        parent_specs = [DelegateSpec(delegate_id="D1", name="Parent Task")]
        result = await mgr.launch_plan("Parent", "desc", parent_specs)
        parent_id = result["plan_id"]
        parent_plan = mgr._plans[parent_id]
        parent_plan.orchestrator_id = "orch-1"

        mgr._get_group_storage().create.return_value = MagicMock(id="group-sub")
        sub_specs = [DelegateSpec(delegate_id="D1", name="Sub Task")]
        sub_result = await mgr.launch_subplan(
            name="SubPlan", description="", delegate_specs=sub_specs,
            parent_plan_id=parent_id, parent_delegate_id="D1",
        )
        sub_id = sub_result["plan_id"]

        crystal = MemoryCrystal(delegate_id="D1", task="Sub Task", summary="Done")
        mgr._crystals[sub_id] = {"D1": crystal}
        mgr._statuses[sub_id]["D1"] = "crystal"
        mgr._plans[sub_id].status = "completed"
        mgr._plans[sub_id].completed_at = time.time()

        mgr._persist_delegate_message.reset_mock()
        await mgr._on_subplan_complete(sub_id)

        # Should have notified parent orchestrator
        orch_calls = [
            c for c in mgr._persist_delegate_message.call_args_list
            if c[0][0] == "orch-1"
        ]
        assert len(orch_calls) >= 1
        assert "SubPlan" in orch_calls[0][0][2]

    @pytest.mark.asyncio
    async def test_non_subplan_completion_skips_rollup(self):
        mgr = _make_mock_manager()

        parent_specs = [DelegateSpec(delegate_id="D1", name="Task")]
        result = await mgr.launch_plan("TopLevel", "desc", parent_specs)
        plan_id = result["plan_id"]

        # Should not raise — just early-returns
        await mgr._on_subplan_complete(plan_id)

    @pytest.mark.asyncio
    async def test_orphaned_subplan_logs_warning(self):
        mgr = _make_mock_manager()

        # Create a plan with parent_plan_id pointing to nonexistent plan
        mgr._get_group_storage().create.return_value = MagicMock(id="group-orphan")
        sub_specs = [DelegateSpec(delegate_id="D1", name="Orphan Task")]
        sub_result = await mgr.launch_subplan(
            name="Orphan", description="", delegate_specs=sub_specs,
            parent_plan_id="nonexistent-plan", parent_delegate_id="D1",
        )
        sub_id = sub_result["plan_id"]
        mgr._plans[sub_id].status = "completed"

        # Should not raise
        await mgr._on_subplan_complete(sub_id)


class TestSwarmLaunchSubplanTool:

    @pytest.mark.asyncio
    async def test_tool_exists_in_swarm_tools(self):
        from app.agents.swarm_tools import create_swarm_tools
        tools = create_swarm_tools("plan-1", "D1", lambda: None)
        names = {t.name for t in tools}
        assert "swarm_launch_subplan" in names

    @pytest.mark.asyncio
    async def test_tool_rejects_invalid_json(self):
        from app.agents.swarm_tools import SwarmLaunchSubplanTool
        mgr = _make_mock_manager()
        mgr._plans["p1"] = TaskPlan(name="test")

        tool = SwarmLaunchSubplanTool({
            "plan_id": "p1",
            "delegate_id": "D1",
            "get_manager": lambda: mgr,
        })

        result = await tool.execute(
            name="Sub", description="desc",
            delegates_json="not valid json",
        )
        assert "Invalid JSON" in result

    @pytest.mark.asyncio
    async def test_tool_rejects_empty_array(self):
        from app.agents.swarm_tools import SwarmLaunchSubplanTool
        mgr = _make_mock_manager()
        mgr._plans["p1"] = TaskPlan(name="test")

        tool = SwarmLaunchSubplanTool({
            "plan_id": "p1",
            "delegate_id": "D1",
            "get_manager": lambda: mgr,
        })

        result = await tool.execute(
            name="Sub", description="desc",
            delegates_json="[]",
        )
        assert "non-empty" in result


class TestMultiLevelNesting:
    """Test 3+ levels of swarm nesting."""

    @pytest.mark.asyncio
    async def test_three_level_nesting(self):
        mgr = _make_mock_manager()

        # Level 1: Top plan
        l1_specs = [DelegateSpec(delegate_id="D1", name="L1-Task")]
        l1_result = await mgr.launch_plan("Level 1", "top", l1_specs)
        l1_id = l1_result["plan_id"]

        # Level 2: Sub-plan
        mgr._get_group_storage().create.return_value = MagicMock(id="group-l2")
        l2_specs = [DelegateSpec(delegate_id="D1", name="L2-Task")]
        l2_result = await mgr.launch_subplan(
            name="Level 2", description="mid", delegate_specs=l2_specs,
            parent_plan_id=l1_id, parent_delegate_id="D1",
        )
        l2_id = l2_result["plan_id"]

        # Level 3: Sub-sub-plan
        mgr._get_group_storage().create.return_value = MagicMock(id="group-l3")
        l3_specs = [DelegateSpec(delegate_id="D1", name="L3-Task")]
        l3_result = await mgr.launch_subplan(
            name="Level 3", description="deep", delegate_specs=l3_specs,
            parent_plan_id=l2_id, parent_delegate_id="D1",
        )
        l3_id = l3_result["plan_id"]

        # All 3 plans tracked
        assert len(mgr._plans) == 3

        # Verify linkage chain
        assert mgr._plans[l1_id].parent_plan_id is None
        assert mgr._plans[l2_id].parent_plan_id == l1_id
        assert mgr._plans[l3_id].parent_plan_id == l2_id

        # Complete L3 → rolls up to L2
        l2_plan = mgr._plans[l2_id]
        l2_plan.orchestrator_id = l2_result["orchestrator_id"]
        l2_initial_tasks = len(l2_plan.task_list)

        mgr._crystals[l3_id] = {"D1": MemoryCrystal(
            delegate_id="D1", task="L3", summary="Deep work done"
        )}
        mgr._statuses[l3_id]["D1"] = "crystal"
        mgr._plans[l3_id].status = "completed"
        mgr._plans[l3_id].completed_at = time.time()
        await mgr._on_subplan_complete(l3_id)

        assert len(l2_plan.task_list) == l2_initial_tasks + 1
        assert "Level 3" in l2_plan.task_list[-1].title
