"""
Tests for swarm coordination tools.

Covers: task list CRUD, locking, concurrent claim prevention,
crystal querying, orchestrator log reading, and dynamic delegate requests.
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.models.delegate import (
    DelegateSpec, DelegateMeta, TaskPlan, MemoryCrystal, SwarmTask,
)
from app.agents.swarm_tools import (
    SwarmTaskListTool,
    SwarmCompleteTaskTool,
    SwarmAddTaskTool,
    SwarmClaimTaskTool,
    SwarmNoteTool,
    SwarmQueryCrystalTool,
    create_swarm_tools,
)

# These may not exist yet (pending diff application)
try:
    from app.agents.swarm_tools import SwarmReadLogTool, SwarmRequestDelegateTool
    HAS_NEW_TOOLS = True
except ImportError:
    HAS_NEW_TOOLS = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeManager:
    """Minimal DelegateManager stub for swarm tool testing."""

    def __init__(self):
        import threading
        self._plans = {}
        self._statuses = {}
        self._crystals = {}
        self._persist_lock = threading.RLock()
        self._persisted_messages = []
        self._group_to_plan = {}

    def _persist_plan(self, plan_id):
        pass  # no-op for testing

    def _persist_delegate_message(self, chat_id, role, content):
        self._persisted_messages.append((chat_id, role, content))

    def _get_chat_storage(self):
        return FakeChatStorage()


class FakeChatStorage:
    def get(self, chat_id):
        return None


@pytest.fixture
def manager():
    return FakeManager()


@pytest.fixture
def plan_with_tasks(manager):
    plan = TaskPlan(
        name="Test Plan",
        description="A test",
        orchestrator_id="orch-1",
        task_list=[
            SwarmTask(task_id="st_D1", title="Auth", status="open", created_at=time.time()),
            SwarmTask(task_id="st_D2", title="Tests", status="open", created_at=time.time()),
            SwarmTask(task_id="st_D3", title="Docs", status="done", summary="Done", created_at=time.time()),
        ],
        delegate_specs=[
            DelegateSpec(delegate_id="D1", name="Auth", scope="do auth"),
            DelegateSpec(delegate_id="D2", name="Tests", scope="write tests"),
            DelegateSpec(delegate_id="D3", name="Docs", scope="write docs"),
        ],
    )
    plan_id = "plan-001"
    manager._plans[plan_id] = plan
    manager._statuses[plan_id] = {"D1": "running", "D2": "running", "D3": "crystal"}
    manager._crystals[plan_id] = {
        "D3": MemoryCrystal(
            delegate_id="D3", task="Docs", summary="Wrote all docs.",
            files_changed=[], decisions=["Used markdown"],
            original_tokens=5000, crystal_tokens=200, created_at=time.time(),
        )
    }
    return plan_id


def make_ctx(manager, plan_id, delegate_id="D1"):
    return {
        "plan_id": plan_id,
        "delegate_id": delegate_id,
        "get_manager": lambda: manager,
    }


# ---------------------------------------------------------------------------
# SwarmTaskListTool
# ---------------------------------------------------------------------------

class TestSwarmTaskList:
    @pytest.mark.asyncio
    async def test_lists_all_tasks(self, manager, plan_with_tasks):
        tool = SwarmTaskListTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute()
        assert "Auth" in result
        assert "Tests" in result
        assert "Docs" in result

    @pytest.mark.asyncio
    async def test_filters_by_status(self, manager, plan_with_tasks):
        tool = SwarmTaskListTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(status_filter="done")
        assert "Docs" in result
        assert "Auth" not in result

    @pytest.mark.asyncio
    async def test_empty_list(self, manager):
        manager._plans["empty"] = TaskPlan(name="Empty", task_list=[])
        tool = SwarmTaskListTool(make_ctx(manager, "empty"))
        result = await tool.execute()
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_no_plan(self, manager):
        tool = SwarmTaskListTool(make_ctx(manager, "nonexistent"))
        result = await tool.execute()
        assert "No active plan" in result


# ---------------------------------------------------------------------------
# SwarmClaimTaskTool
# ---------------------------------------------------------------------------

class TestSwarmClaimTask:
    @pytest.mark.asyncio
    async def test_claim_open_task(self, manager, plan_with_tasks):
        tool = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(task_id="st_D1")
        assert "Claimed" in result
        task = manager._plans[plan_with_tasks].task_list[0]
        assert task.status == "claimed"
        assert task.claimed_by == "D1"

    @pytest.mark.asyncio
    async def test_claim_already_claimed_by_other(self, manager, plan_with_tasks):
        plan = manager._plans[plan_with_tasks]
        plan.task_list[0].status = "claimed"
        plan.task_list[0].claimed_by = "D2"
        tool = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(task_id="st_D1")
        assert "already claimed" in result

    @pytest.mark.asyncio
    async def test_claim_done_task(self, manager, plan_with_tasks):
        tool = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(task_id="st_D3")
        assert "already done" in result

    @pytest.mark.asyncio
    async def test_claim_nonexistent(self, manager, plan_with_tasks):
        tool = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(task_id="st_NOPE")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_reclaim_own_task(self, manager, plan_with_tasks):
        plan = manager._plans[plan_with_tasks]
        plan.task_list[0].status = "claimed"
        plan.task_list[0].claimed_by = "D1"
        tool = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(task_id="st_D1")
        assert "Claimed" in result


# ---------------------------------------------------------------------------
# SwarmCompleteTaskTool
# ---------------------------------------------------------------------------

class TestSwarmCompleteTask:
    @pytest.mark.asyncio
    async def test_complete_task(self, manager, plan_with_tasks):
        tool = SwarmCompleteTaskTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(task_id="st_D1", summary="Implemented auth")
        assert "done" in result.lower() or "✅" in result
        task = manager._plans[plan_with_tasks].task_list[0]
        assert task.status == "done"
        assert task.summary == "Implemented auth"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_nonexistent(self, manager, plan_with_tasks):
        tool = SwarmCompleteTaskTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(task_id="st_NOPE", summary="x")
        assert "not found" in result


# ---------------------------------------------------------------------------
# SwarmAddTaskTool
# ---------------------------------------------------------------------------

class TestSwarmAddTask:
    @pytest.mark.asyncio
    async def test_add_task(self, manager, plan_with_tasks):
        tool = SwarmAddTaskTool(make_ctx(manager, plan_with_tasks, "D2"))
        result = await tool.execute(title="Fix migration", tags="db,urgent")
        assert "added" in result.lower()
        plan = manager._plans[plan_with_tasks]
        new_task = plan.task_list[-1]
        assert new_task.title == "Fix migration"
        assert new_task.added_by == "D2"
        assert "db" in new_task.tags
        assert "urgent" in new_task.tags

    @pytest.mark.asyncio
    async def test_add_task_empty_tags(self, manager, plan_with_tasks):
        tool = SwarmAddTaskTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(title="Something")
        plan = manager._plans[plan_with_tasks]
        new_task = plan.task_list[-1]
        assert new_task.tags == []


# ---------------------------------------------------------------------------
# SwarmNoteTool
# ---------------------------------------------------------------------------

class TestSwarmNote:
    @pytest.mark.asyncio
    async def test_posts_to_orchestrator(self, manager, plan_with_tasks):
        tool = SwarmNoteTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(message="DB schema changed to v2")
        assert "posted" in result.lower()
        assert len(manager._persisted_messages) == 1
        _, _, content = manager._persisted_messages[0]
        assert "D1 → all" in content
        assert "DB schema changed" in content


# ---------------------------------------------------------------------------
# SwarmQueryCrystalTool
# ---------------------------------------------------------------------------

class TestSwarmQueryCrystal:
    @pytest.mark.asyncio
    async def test_list_available(self, manager, plan_with_tasks):
        tool = SwarmQueryCrystalTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute()
        assert "D3" in result
        assert "Docs" in result

    @pytest.mark.asyncio
    async def test_query_specific(self, manager, plan_with_tasks):
        tool = SwarmQueryCrystalTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(delegate_id="D3")
        assert "Wrote all docs" in result
        assert "markdown" in result.lower()

    @pytest.mark.asyncio
    async def test_query_nonexistent(self, manager, plan_with_tasks):
        tool = SwarmQueryCrystalTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(delegate_id="D99")
        assert "No crystal" in result

    @pytest.mark.asyncio
    async def test_no_crystals_yet(self, manager):
        manager._plans["p"] = TaskPlan(name="P")
        manager._crystals["p"] = {}
        tool = SwarmQueryCrystalTool(make_ctx(manager, "p"))
        result = await tool.execute()
        assert "No crystals available" in result


# ---------------------------------------------------------------------------
# SwarmReadLogTool
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_NEW_TOOLS, reason="SwarmReadLogTool not yet applied")
class TestSwarmReadLog:
    @pytest.mark.asyncio
    async def test_no_orchestrator(self, manager):
        manager._plans["p"] = TaskPlan(name="P", orchestrator_id=None)
        tool = SwarmReadLogTool(make_ctx(manager, "p"))
        result = await tool.execute()
        assert "No orchestrator" in result

    @pytest.mark.asyncio
    async def test_reads_messages(self, manager, plan_with_tasks):
        # Set up a fake chat with messages
        class FakeMsg:
            def __init__(self, c):
                self.content = c
        class FakeChat:
            def __init__(self):
                self.messages = [
                    FakeMsg("**orchestrator → all:** Launch 3 delegates"),
                    FakeMsg("**D3 → orchestrator:** [Crystal received] Wrote all docs"),
                    FakeMsg("**orchestrator → D3:** Accepted. Good coverage."),
                ]
        class FakeCS:
            def get(self, cid):
                return FakeChat()

        manager._get_chat_storage = lambda: FakeCS()
        tool = SwarmReadLogTool(make_ctx(manager, plan_with_tasks))
        result = await tool.execute(last_n=2)
        assert "Crystal received" in result
        assert "Accepted" in result
        # Should NOT include the first message (only last 2)
        assert "Launch 3" not in result


# ---------------------------------------------------------------------------
# SwarmRequestDelegateTool
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_NEW_TOOLS, reason="SwarmRequestDelegateTool not yet applied")
class TestSwarmRequestDelegate:
    @pytest.mark.asyncio
    async def test_creates_spec_and_task(self, manager, plan_with_tasks):
        # Mock _spawn_and_start_dynamic_delegate to avoid real infra
        manager._spawn_and_start_dynamic_delegate = AsyncMock()

        tool = SwarmRequestDelegateTool(make_ctx(manager, plan_with_tasks, "D1"))
        result = await tool.execute(
            name="Migration fix",
            scope="Fix the DB migration",
            files="src/db/migrate.py",
        )
        assert "Requested new delegate" in result
        plan = manager._plans[plan_with_tasks]
        # New spec should be appended
        new_spec = plan.delegate_specs[-1]
        assert new_spec.name == "Migration fix"
        assert new_spec.scope == "Fix the DB migration"
        assert "src/db/migrate.py" in new_spec.files
        assert new_spec.emoji == "🆕"
        # New task on shared list
        new_task = plan.task_list[-1]
        assert new_task.title == "Migration fix"
        assert new_task.added_by == "D1"
        # Should have been noted in orchestrator
        assert any("Migration fix" in m[2] for m in manager._persisted_messages)

    @pytest.mark.asyncio
    async def test_unique_id_generation(self, manager, plan_with_tasks):
        manager._spawn_and_start_dynamic_delegate = AsyncMock()
        tool = SwarmRequestDelegateTool(make_ctx(manager, plan_with_tasks, "D1"))
        await tool.execute(name="Task A", scope="A")
        await tool.execute(name="Task B", scope="B")
        plan = manager._plans[plan_with_tasks]
        new_ids = [s.delegate_id for s in plan.delegate_specs if s.delegate_id.startswith("D")]
        # All IDs should be unique
        assert len(new_ids) == len(set(new_ids))


# ---------------------------------------------------------------------------
# create_swarm_tools factory
# ---------------------------------------------------------------------------

class TestCreateSwarmTools:
    def test_returns_all_tools(self, manager, plan_with_tasks):
        tools = create_swarm_tools(plan_with_tasks, "D1", lambda: manager)
        names = {t.name for t in tools}
        assert "swarm_task_list" in names
        assert "swarm_complete_task" in names
        assert "swarm_add_task" in names
        assert "swarm_claim_task" in names
        assert "swarm_note" in names
        assert "swarm_query_crystal" in names
        if HAS_NEW_TOOLS:
            assert "swarm_read_log" in names
            assert "swarm_request_delegate" in names
            assert "swarm_launch_subplan" in names
            assert len(tools) == 9
        else:
            assert len(tools) == 6


# ---------------------------------------------------------------------------
# Locking / concurrency
# ---------------------------------------------------------------------------

class TestSwarmLocking:
    @pytest.mark.asyncio
    async def test_concurrent_claims_one_wins(self, manager, plan_with_tasks):
        """Two delegates claiming the same task — only one should succeed."""
        tool_d1 = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        tool_d2 = SwarmClaimTaskTool(make_ctx(manager, plan_with_tasks, "D2"))

        results = await asyncio.gather(
            tool_d1.execute(task_id="st_D2"),
            tool_d2.execute(task_id="st_D2"),
        )
        # One should claim, one should get "already claimed"
        claimed_count = sum(1 for r in results if "Claimed" in r or "🔒" in r)
        rejected_count = sum(1 for r in results if "already claimed" in r)
        # Due to RLock serialization, exactly one wins
        assert claimed_count == 1
        assert rejected_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_adds_both_succeed(self, manager, plan_with_tasks):
        """Two delegates adding tasks — both should succeed."""
        tool_d1 = SwarmAddTaskTool(make_ctx(manager, plan_with_tasks, "D1"))
        tool_d2 = SwarmAddTaskTool(make_ctx(manager, plan_with_tasks, "D2"))

        results = await asyncio.gather(
            tool_d1.execute(title="Task from D1"),
            tool_d2.execute(title="Task from D2"),
        )
        assert all("added" in r.lower() for r in results)
        plan = manager._plans[plan_with_tasks]
        titles = [t.title for t in plan.task_list]
        assert "Task from D1" in titles
        assert "Task from D2" in titles
