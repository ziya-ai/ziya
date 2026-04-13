"""
Tests for DelegateManager (T24).

Tests the core orchestration logic: plan creation, dependency tracking,
crystal propagation, and concurrency control.

Uses mocks to avoid actual Bedrock API calls and file I/O.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, mock_open, patch, PropertyMock

import pytest

from app.models.delegate import (
    DelegateBudget,
    DelegateMeta,
    DelegateSpec,
    MemoryCrystal,
    SwarmBudget,
    TaskPlan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project directory structure."""
    project_dir = tmp_path / "projects" / "test-project"
    (project_dir / "chats").mkdir(parents=True)
    (project_dir / "contexts").mkdir(parents=True)
    # Create empty _groups.json
    groups_file = project_dir / "chats" / "_groups.json"
    groups_file.write_text(json.dumps({"version": 1, "groups": []}))
    return project_dir


@pytest.fixture
def manager(tmp_project):
    """Create a DelegateManager with test project."""
    from app.agents.delegate_manager import DelegateManager, reset_delegate_manager
    reset_delegate_manager()
    mgr = DelegateManager(
        project_id="test",
        project_dir=tmp_project,
    )
    return mgr


# ---------------------------------------------------------------------------
# Tests for _post_progress_to_source — inline artifact report embedding
# ---------------------------------------------------------------------------

def _make_crystal_with_artifacts(delegate_id: str, artifact_paths: list) -> MemoryCrystal:
    """Build a MemoryCrystal that references artifact files."""
    from app.models.delegate import FileChange
    return MemoryCrystal(
        delegate_id=delegate_id,
        task="Research task",
        summary="Detailed research summary.",
        decisions=["Decision A"],
        files_changed=[
            FileChange(path=p, action="created", line_delta="(new)")
            for p in artifact_paths
        ],
    )


class TestPostProgressInlineArtifacts:
    """
    _post_progress_to_source should embed artifact file contents as
    <details> blocks rather than just saying "N report(s) written".
    """

    def _setup_plan(self, manager):
        plan_id = "plan-artifact-test"
        spec = DelegateSpec(
            delegate_id="d1", name="Research Agent", emoji="🔬",
            project_root="/fake/root",
        )
        plan = TaskPlan(
            name="Test Plan",
            source_conversation_id="src-conv-1",
            delegate_specs=[spec],
            created_at=time.time(),
        )
        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {"d1": "crystal"}
        return plan_id, spec, plan

    def test_artifact_content_embedded_as_details(self, manager):
        """Report content is embedded inline as a <details> collapsible block."""
        plan_id, spec, plan = self._setup_plan(manager)
        crystal = _make_crystal_with_artifacts(
            "d1",
            [".ziya/tasks/plan-abc/research-agent/analysis.md"],
        )
        report_content = "# Analysis\n\nHere are my findings."
        posted_messages = []

        mock_cs = MagicMock()
        mock_cs.add_message.side_effect = lambda cid, m: posted_messages.append(m)

        with patch.object(manager, "_get_chat_storage", return_value=mock_cs), \
             patch("builtins.open", mock_open(read_data=report_content)), \
             patch("app.context.get_project_root", return_value="/fake/root"):
            manager._post_progress_to_source(plan_id, "d1", crystal)

        assert len(posted_messages) == 1
        content = posted_messages[0].content
        assert "<details>" in content
        assert "<summary>" in content
        assert "analysis" in content
        assert report_content in content
        assert "report(s) written" not in content

    def test_no_reports_no_details_block(self, manager):
        """Crystals with only source-file changes don't add <details> blocks."""
        plan_id, spec, plan = self._setup_plan(manager)
        from app.models.delegate import FileChange
        crystal = MemoryCrystal(
            delegate_id="d1",
            task="Code task",
            summary="Changed some files.",
            files_changed=[FileChange(path="src/main.py", action="modified", line_delta="+5 -2")],
        )
        posted_messages = []
        mock_cs = MagicMock()
        mock_cs.add_message.side_effect = lambda cid, m: posted_messages.append(m)

        with patch.object(manager, "_get_chat_storage", return_value=mock_cs):
            manager._post_progress_to_source(plan_id, "d1", crystal)

        assert len(posted_messages) == 1
        assert "<details>" not in posted_messages[0].content
        assert "report(s) written" not in posted_messages[0].content

    def test_unreadable_artifact_shows_fallback(self, manager):
        """If an artifact file cannot be read, a graceful fallback is shown."""
        plan_id, spec, plan = self._setup_plan(manager)
        crystal = _make_crystal_with_artifacts(
            "d1",
            [".ziya/tasks/plan-abc/research-agent/analysis.md"],
        )
        posted_messages = []
        mock_cs = MagicMock()
        mock_cs.add_message.side_effect = lambda cid, m: posted_messages.append(m)

        with patch.object(manager, "_get_chat_storage", return_value=mock_cs), \
             patch("builtins.open", side_effect=OSError("No such file")), \
             patch("app.context.get_project_root", return_value="/fake/root"):
            manager._post_progress_to_source(plan_id, "d1", crystal)

        assert len(posted_messages) == 1
        content = posted_messages[0].content
        assert "<details>" in content
        assert "could not read" in content


@pytest.fixture
def simple_specs():
    """Two independent delegates (no dependencies)."""
    return [
        DelegateSpec(
            delegate_id="d1",
            name="Auth Module",
            emoji="🔐",
            scope="Implement OAuth2 provider",
            files=["auth/provider.py"],
            dependencies=[],
        ),
        DelegateSpec(
            delegate_id="d2",
            name="Test Suite",
            emoji="🧪",
            scope="Write integration tests",
            files=["tests/test_auth.py"],
            dependencies=[],
        ),
    ]


@pytest.fixture
def chained_specs():
    """Three delegates with D3 depending on D1 and D2."""
    return [
        DelegateSpec(
            delegate_id="d1",
            name="Auth Module",
            emoji="🔐",
            scope="Implement OAuth2",
            files=["auth/provider.py"],
            dependencies=[],
        ),
        DelegateSpec(
            delegate_id="d2",
            name="Token Manager",
            emoji="🔑",
            scope="Implement token refresh",
            files=["auth/tokens.py"],
            dependencies=[],
        ),
        DelegateSpec(
            delegate_id="d3",
            name="Integration Tests",
            emoji="🧪",
            scope="Test auth + tokens together",
            files=["tests/test_integration.py"],
            dependencies=["d1", "d2"],
        ),
    ]


def make_crystal(delegate_id: str, task: str = "") -> MemoryCrystal:
    """Helper to create a test crystal."""
    return MemoryCrystal(
        delegate_id=delegate_id,
        task=task or f"Task for {delegate_id}",
        summary=f"Completed {delegate_id}",
        files_changed=[],
        decisions=["Used approach A"],
        exports={},
        tool_stats={"file_write": 1},
        original_tokens=5000,
        crystal_tokens=300,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Unit tests — dependency resolution (no I/O)
# ---------------------------------------------------------------------------

class TestDependencyResolution:
    """Test the DAG resolution logic without any streaming."""

    def test_no_deps_all_ready(self, manager, simple_specs):
        """Delegates with no dependencies should be immediately ready."""
        plan_id = "test-plan"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {s.delegate_id: "proposed" for s in simple_specs}
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = set()
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        # Mock _start_delegate to avoid actual execution
        manager._start_delegate = AsyncMock()

        asyncio.run(manager._resolve_and_start(plan_id))

        assert manager._statuses[plan_id]["d1"] == "ready"
        assert manager._statuses[plan_id]["d2"] == "ready"
        assert manager._start_delegate.call_count == 2

    def test_deps_block_until_crystal(self, manager, chained_specs):
        """D3 should stay proposed until D1 and D2 have crystals."""
        plan_id = "test-plan"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {
            "d1": "proposed", "d2": "proposed", "d3": "proposed"
        }
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = set()
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None
        manager._start_delegate = AsyncMock()

        asyncio.run(manager._resolve_and_start(plan_id))

        # D1 and D2 should be ready, D3 should still be proposed
        assert manager._statuses[plan_id]["d1"] == "ready"
        assert manager._statuses[plan_id]["d2"] == "ready"
        assert manager._statuses[plan_id]["d3"] == "proposed"
        assert manager._start_delegate.call_count == 2

    def test_deps_unblock_after_crystals(self, manager, chained_specs):
        """D3 should become ready after both D1 and D2 produce crystals."""
        plan_id = "test-plan"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {
            "d1": "crystal", "d2": "crystal", "d3": "proposed"
        }
        manager._crystals[plan_id] = {
            "d1": make_crystal("d1"),
            "d2": make_crystal("d2"),
        }
        manager._running[plan_id] = set()
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None
        manager._start_delegate = AsyncMock()

        asyncio.run(manager._resolve_and_start(plan_id))

        assert manager._statuses[plan_id]["d3"] == "ready"
        assert manager._start_delegate.call_count == 1

    def test_partial_deps_still_blocked(self, manager, chained_specs):
        """D3 stays proposed if only D1 has a crystal but D2 doesn't."""
        plan_id = "test-plan"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {
            "d1": "crystal", "d2": "running", "d3": "proposed"
        }
        manager._crystals[plan_id] = {"d1": make_crystal("d1")}
        manager._running[plan_id] = {"d2"}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None
        manager._start_delegate = AsyncMock()

        asyncio.run(manager._resolve_and_start(plan_id))

        assert manager._statuses[plan_id]["d3"] == "proposed"
        assert manager._start_delegate.call_count == 0


class TestPlanCompletion:
    """Test plan completion detection."""

    def test_all_crystals_means_complete(self, manager):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=[], status="running", created_at=time.time())
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "crystal"}
        assert manager._is_plan_complete(plan_id) is True

    def test_mixed_terminal_means_complete(self, manager):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=[], status="running", created_at=time.time())
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "failed"}
        assert manager._is_plan_complete(plan_id) is True

    def test_running_means_not_complete(self, manager):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=[], status="running", created_at=time.time())
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "running"}
        assert manager._is_plan_complete(plan_id) is False

    def test_proposed_means_not_complete(self, manager):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=[], status="running", created_at=time.time())
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "proposed"}
        assert manager._is_plan_complete(plan_id) is False


class TestUpstreamCrystals:
    """Test crystal retrieval for downstream delegates."""

    def test_get_upstream_crystals(self, manager, chained_specs):
        plan_id = "p1"
        c1 = make_crystal("d1", "Auth Module")
        c2 = make_crystal("d2", "Token Manager")
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._crystals[plan_id] = {"d1": c1, "d2": c2}

        upstream = manager.get_upstream_crystals(plan_id, "d3")
        assert len(upstream) == 2
        assert upstream[0].delegate_id == "d1"
        assert upstream[1].delegate_id == "d2"

    def test_no_upstream_for_root_delegate(self, manager, simple_specs):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._crystals[plan_id] = {}

        upstream = manager.get_upstream_crystals(plan_id, "d1")
        assert len(upstream) == 0


class TestSwarmBudget:
    """Test SwarmBudget calculation."""

    def test_budget_with_crystals(self, manager, simple_specs):
        plan_id = "p1"
        c1 = make_crystal("d1")
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "running"}
        manager._crystals[plan_id] = {"d1": c1}

        budget = manager.get_swarm_budget(plan_id)
        assert budget is not None
        assert "d1" in budget.delegates
        assert "d2" in budget.delegates
        assert budget.delegates["d1"].status == "crystal"
        assert budget.delegates["d2"].status == "running"
        assert budget.total_freed == c1.original_tokens - c1.crystal_tokens


class TestMessageBuilding:
    """Test delegate message construction."""

    def test_messages_include_upstream_context(self, manager, chained_specs):
        plan_id = "p1"
        c1 = make_crystal("d1", "Auth Module")
        c1.decisions = ["Used OAuth2 code grant"]
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._crystals[plan_id] = {"d1": c1}

        d3_spec = chained_specs[2]
        messages = manager._build_delegate_messages(plan_id, d3_spec)

        assert len(messages) == 1
        content = messages[0]["content"]
        assert "Prior work: Auth Module" in content
        assert "Used OAuth2 code grant" in content
        assert "Integration Tests" in content

    def test_messages_without_upstream(self, manager, simple_specs):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._crystals[plan_id] = {}

        messages = manager._build_delegate_messages(plan_id, simple_specs[0])
        assert len(messages) == 1
        content = messages[0]["content"]
        assert "Prior work" not in content
        assert "Auth Module" in content


class TestCrystalReady:
    """Test the on_crystal_ready handler."""

    def test_crystal_updates_status(self, manager, simple_specs):
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {"d1": "running", "d2": "running"}
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = {"d1", "d2"}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        crystal = make_crystal("d1")

        # Mock persistence methods
        manager._patch_chat_crystal = MagicMock()
        manager._persist_plan = MagicMock()
        manager._start_delegate = AsyncMock()

        asyncio.run(manager.on_crystal_ready(plan_id, "d1", crystal))

        assert manager._statuses[plan_id]["d1"] == "crystal"
        assert "d1" in manager._crystals[plan_id]
        assert "d1" not in manager._running[plan_id]

    def test_crystal_triggers_downstream(self, manager, chained_specs):
        """When D1 + D2 both crystal, D3 should become ready."""
        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=chained_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {
            "d1": "crystal", "d2": "running", "d3": "proposed"
        }
        manager._crystals[plan_id] = {"d1": make_crystal("d1")}
        manager._running[plan_id] = {"d2"}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        manager._patch_chat_crystal = MagicMock()
        manager._persist_plan = MagicMock()
        manager._start_delegate = AsyncMock()

        # D2 completes
        c2 = make_crystal("d2")
        asyncio.run(manager.on_crystal_ready(plan_id, "d2", c2))

        # D3 should now be ready and started
        assert manager._statuses[plan_id]["d3"] == "ready"
        assert manager._start_delegate.call_count == 1

    def test_plan_completes_when_all_done(self, manager, simple_specs):
        plan_id = "p1"
        plan = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {"d1": "crystal", "d2": "running"}
        manager._crystals[plan_id] = {"d1": make_crystal("d1")}
        manager._running[plan_id] = {"d2"}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        manager._patch_chat_crystal = MagicMock()
        manager._persist_plan = MagicMock()
        manager._start_delegate = AsyncMock()

        c2 = make_crystal("d2")
        asyncio.run(manager.on_crystal_ready(plan_id, "d2", c2))

        assert plan.status == "completed"
        assert plan.completed_at is not None


class TestProgressCallback:
    """Test that progress events fire correctly."""

    def test_callback_fires_on_crystal(self, manager, simple_specs):
        events = []

        async def capture(plan_id, delegate_id, event, data):
            events.append((event, delegate_id, data))

        plan_id = "p1"
        manager._plans[plan_id] = TaskPlan(
            name="Test", delegate_specs=simple_specs, created_at=time.time()
        )
        manager._statuses[plan_id] = {"d1": "running", "d2": "proposed"}
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = {"d1"}
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = capture

        manager._patch_chat_crystal = MagicMock()
        manager._persist_plan = MagicMock()
        manager._start_delegate = AsyncMock()

        asyncio.run(manager.on_crystal_ready(plan_id, "d1", make_crystal("d1")))

        crystal_events = [e for e in events if e[0] == "crystal"]
        assert len(crystal_events) == 1
        assert crystal_events[0][1] == "d1"


class TestSingleton:
    """Test the module-level singleton."""

    def test_get_and_reset(self, tmp_project):
        from app.agents.delegate_manager import (
            get_delegate_manager,
            reset_delegate_manager,
        )
        reset_delegate_manager()
        m1 = get_delegate_manager("test", tmp_project)
        m2 = get_delegate_manager("test")
        assert m1 is m2

        reset_delegate_manager()
        m3 = get_delegate_manager("test2", tmp_project)
        assert m3 is not m1
        reset_delegate_manager()
