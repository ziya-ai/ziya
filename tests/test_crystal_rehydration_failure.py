"""
Tests for crystal rehydration failure handling (#18).

Verifies that:
  - Corrupted crystal data logs an error instead of silently passing
  - Failed rehydration marks the delegate as "crystal_degraded"
  - Downstream delegates still resolve (degraded deps count as satisfied)
  - get_upstream_crystals returns [] for degraded dependencies
  - The rehydration summary log includes degraded count
"""

import time
import logging
import pytest
from unittest.mock import MagicMock

from app.models.delegate import (
    DelegateMeta, DelegateSpec, MemoryCrystal, TaskPlan, SwarmTask,
)
from app.agents.delegate_manager import DelegateManager, reset_delegate_manager


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_delegate_manager()
    yield
    reset_delegate_manager()


@pytest.fixture
def manager(tmp_path):
    return DelegateManager("test-project", tmp_path)


def make_spec(did, deps=None):
    return DelegateSpec(
        delegate_id=did,
        name=f"Delegate-{did}",
        scope=f"Scope for {did}",
        files=[],
        dependencies=deps or [],
        emoji="🔵",
    )


def make_crystal(did, summary="Done", tokens=(5000, 200)):
    return MemoryCrystal(
        delegate_id=did, task=f"task-{did}",
        summary=summary,
        original_tokens=tokens[0], crystal_tokens=tokens[1],
        created_at=time.time(),
    )


def _setup_persisted_plan(manager, group_id, plan_id, specs, delegate_statuses,
                          crystal_overrides=None):
    """Helper: set up mock storage as if a plan was persisted.

    crystal_overrides: dict mapping delegate_id -> raw crystal dict (or invalid data).
    """
    groups = []
    chats = []

    plan = TaskPlan(
        name="Test Plan", description="Rehydration test",
        delegate_specs=specs, status="running",
        created_at=time.time(),
    )
    group = MagicMock()
    group.id = group_id
    group.taskPlan = plan.model_dump()
    groups.append(group)

    crystal_overrides = crystal_overrides or {}

    for spec, status in zip(specs, delegate_statuses):
        chat = MagicMock()
        chat.id = spec.conversation_id or f"chat_{spec.delegate_id}"
        meta = DelegateMeta(
            role="delegate", plan_id=plan_id,
            delegate_id=spec.delegate_id,
            status=status,
        ).model_dump()

        # Inject crystal data if provided
        if spec.delegate_id in crystal_overrides:
            meta["crystal"] = crystal_overrides[spec.delegate_id]

        chat.delegateMeta = meta
        chat.messages = []
        chats.append(chat)

    gs = MagicMock()
    gs.list.return_value = groups
    cs = MagicMock()
    cs.list.return_value = chats

    manager._get_group_storage = MagicMock(return_value=gs)
    manager._get_chat_storage = MagicMock(return_value=cs)
    return plan


# ── Tests ─────────────────────────────────────────────────────────────

class TestCrystalRehydrationFailure:
    """Tests for crystal rehydration error handling (#18)."""

    def test_valid_crystal_rehydrates_normally(self, manager):
        """Baseline: valid crystal data rehydrates without issues."""
        specs = [make_spec("D1")]
        crystal_data = make_crystal("D1").model_dump()

        _setup_persisted_plan(
            manager, "g1", "p1", specs, ["crystal"],
            crystal_overrides={"D1": crystal_data}
        )
        count = manager.rehydrate()

        assert count == 1
        assert "D1" in manager._crystals.get("p1", {})
        assert manager._statuses["p1"]["D1"] == "crystal"

    def test_corrupted_crystal_sets_degraded_status(self, manager):
        """Corrupted crystal data should set crystal_degraded status, not crystal."""
        specs = [make_spec("D1")]
        # Invalid crystal data — missing required fields
        bad_crystal = {"not_a_real_field": "garbage"}

        _setup_persisted_plan(
            manager, "g1", "p1", specs, ["crystal"],
            crystal_overrides={"D1": bad_crystal}
        )

        count = manager.rehydrate()

        assert count == 1
        # Status should be crystal_degraded, not crystal
        assert manager._statuses["p1"]["D1"] == "crystal_degraded"
        # No crystal should be stored
        assert "D1" not in manager._crystals.get("p1", {})

    def test_corrupted_crystal_logs_error(self, manager):
        """Corrupted crystal data should produce a visible error log."""
        from unittest.mock import patch as mock_patch
        specs = [make_spec("D1")]
        bad_crystal = {"not_a_real_field": "garbage"}

        _setup_persisted_plan(
            manager, "g1", "p1", specs, ["crystal"],
            crystal_overrides={"D1": bad_crystal}
        )

        # Intercept the logger.error call in delegate_manager
        with mock_patch("app.agents.delegate_manager.logger") as mock_logger:
            # Forward info calls so rehydration completes normally
            mock_logger.info = MagicMock()
            mock_logger.warning = MagicMock()
            mock_logger.debug = MagicMock()
            mock_logger.error = MagicMock()

            manager.rehydrate()

            # Verify logger.error was called with the crystal failure message
            error_calls = [str(c) for c in mock_logger.error.call_args_list]
            assert any("Crystal rehydration failed" in call for call in error_calls), \
                f"Expected 'Crystal rehydration failed' in error log calls, got: {error_calls}"

    def test_corrupted_crystal_does_not_crash_rehydration(self, manager):
        """One bad crystal should not prevent other delegates from rehydrating."""
        specs = [make_spec("D1"), make_spec("D2")]
        good_crystal = make_crystal("D2").model_dump()
        bad_crystal = {"invalid": True}

        _setup_persisted_plan(
            manager, "g1", "p1", specs, ["crystal", "crystal"],
            crystal_overrides={"D1": bad_crystal, "D2": good_crystal}
        )
        count = manager.rehydrate()

        assert count == 1
        # D1 failed, D2 succeeded
        assert manager._statuses["p1"]["D1"] == "crystal_degraded"
        assert manager._statuses["p1"]["D2"] == "crystal"
        assert "D1" not in manager._crystals.get("p1", {})
        assert "D2" in manager._crystals.get("p1", {})

    @pytest.mark.asyncio
    async def test_downstream_delegate_runs_with_degraded_upstream(self, manager):
        """Downstream delegates should still be schedulable when upstream is degraded."""
        specs = [
            make_spec("D1"),
            make_spec("D2", deps=["D1"]),  # D2 depends on D1
        ]

        # Simulate: D1 is crystal_degraded, D2 is proposed
        plan_id = "p1"
        plan = TaskPlan(
            name="Test", description="Degraded dep test",
            delegate_specs=specs, status="running",
            created_at=time.time(),
        )
        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {
            "D1": "crystal_degraded",
            "D2": "proposed",
        }
        manager._crystals[plan_id] = {}
        manager._running[plan_id] = set()
        manager._tasks[plan_id] = {}
        manager._callbacks[plan_id] = None

        # Mock _start_delegate to just record calls
        started = []
        async def mock_start(pid, spec):
            started.append(spec.delegate_id)
        manager._start_delegate = mock_start

        await manager._resolve_and_start(plan_id)

        # D2 should have been marked ready and started
        assert "D2" in started, \
            f"D2 should run even with degraded upstream, started: {started}"
        assert manager._statuses[plan_id]["D2"] in ("ready", "running")

    def test_degraded_upstream_provides_no_crystal_context(self, manager):
        """get_upstream_crystals should return [] for degraded dependencies."""
        specs = [
            make_spec("D1"),
            make_spec("D2", deps=["D1"]),
        ]
        plan_id = "p1"
        plan = TaskPlan(
            name="Test", description="No crystal context test",
            delegate_specs=specs, status="running",
            created_at=time.time(),
        )
        manager._plans[plan_id] = plan
        manager._statuses[plan_id] = {"D1": "crystal_degraded", "D2": "proposed"}
        manager._crystals[plan_id] = {}  # No crystal stored for D1

        upstream = manager.get_upstream_crystals(plan_id, "D2")
        assert upstream == [], \
            f"Degraded upstream should provide no crystal context, got: {upstream}"

    def test_log_includes_degraded_count(self, manager):
        """Rehydration summary should mention degraded crystal count."""
        from unittest.mock import patch as mock_patch
        specs = [make_spec("D1")]
        bad_crystal = {"completely": "wrong"}

        _setup_persisted_plan(
            manager, "g1", "p1", specs, ["crystal"],
            crystal_overrides={"D1": bad_crystal}
        )

        with mock_patch("app.agents.delegate_manager.logger") as mock_logger:
            mock_logger.info = MagicMock()
            mock_logger.warning = MagicMock()
            mock_logger.debug = MagicMock()
            mock_logger.error = MagicMock()

            manager.rehydrate()

            # Find the rehydration summary in logger.info calls
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            rehydrate_calls = [c for c in info_calls if "Rehydrated plan" in c]
            assert len(rehydrate_calls) >= 1, \
                f"Expected a rehydration log, got info calls: {info_calls}"
            assert any("degraded" in c.lower() for c in rehydrate_calls), \
                f"Rehydration log should mention 'degraded', got: {rehydrate_calls}"
