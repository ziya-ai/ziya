"""
Regression tests for PenPal #45 [HIGH, CWE-400]: unbounded DelegateManager
instance cache keyed by attacker-controlled project_id.

project_id is a client-supplied URL path parameter (app/api/delegates.py)
with no validation that it corresponds to a real, existing project --
get_project_dir() is a pure string join. A client repeatedly hitting a
delegate endpoint with distinct arbitrary project_id values grew the
module-level _instances cache without bound: one DelegateManager (with
its own asyncio state and locks) per distinct value, never evicted.

Fix: _instances is now bounded the same way FileStateManager bounds its
per-conversation cache -- a hard cap (_MAX_DELEGATE_MANAGER_INSTANCES)
plus an idle TTL (_DELEGATE_MANAGER_IDLE_TTL_SECONDS), evicting
oldest-by-last-use first. A manager holding any 'running' plan is never
evicted, since its asyncio Tasks hold live references.
"""

import time
import pytest

from app.agents import delegate_manager as dm_module
from app.agents.delegate_manager import (
    get_delegate_manager,
    reset_delegate_manager,
    DelegateManager,
)
from app.models.delegate import TaskPlan


@pytest.fixture(autouse=True)
def _reset_between_tests():
    reset_delegate_manager()
    yield
    reset_delegate_manager()


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return d


class TestUnboundedGrowthIsCapped:
    """The core fix: hitting the cache with many distinct project_ids no
    longer grows it without bound."""

    def test_instances_capped_at_max(self, project_dir):
        cap = dm_module._MAX_DELEGATE_MANAGER_INSTANCES
        for i in range(cap + 20):
            get_delegate_manager(f"proj-{i}", project_dir)

        assert len(dm_module._instances) <= cap

    def test_oldest_evicted_first_when_over_cap(self, project_dir):
        cap = dm_module._MAX_DELEGATE_MANAGER_INSTANCES
        get_delegate_manager("proj-oldest", project_dir)
        for i in range(cap):
            get_delegate_manager(f"proj-{i}", project_dir)

        # "proj-oldest" was the first ever touched and never re-touched --
        # it must be among the evicted, not still present.
        assert "proj-oldest" not in dm_module._instances

    def test_recently_used_instance_survives_eviction_pressure(self, project_dir):
        cap = dm_module._MAX_DELEGATE_MANAGER_INSTANCES
        get_delegate_manager("proj-recent", project_dir)
        for i in range(cap + 10):
            get_delegate_manager(f"proj-{i}", project_dir)
            # Re-touch "proj-recent" on every iteration so its last_used
            # timestamp stays fresh relative to the flood of new entries.
            get_delegate_manager("proj-recent", project_dir)

        assert "proj-recent" in dm_module._instances


class TestIdleTTLEviction:
    """A manager idle past the TTL is evicted even under the cap."""

    def test_stale_manager_evicted_after_ttl(self, project_dir):
        get_delegate_manager("proj-stale", project_dir)
        assert "proj-stale" in dm_module._instances

        # Simulate the passage of time by backdating last_used.
        dm_module._instance_last_used["proj-stale"] = (
            time.time() - dm_module._DELEGATE_MANAGER_IDLE_TTL_SECONDS - 1
        )

        # Any subsequent call triggers the eviction sweep.
        get_delegate_manager("proj-trigger", project_dir)

        assert "proj-stale" not in dm_module._instances

    def test_fresh_manager_not_evicted_by_ttl_sweep(self, project_dir):
        get_delegate_manager("proj-fresh", project_dir)
        get_delegate_manager("proj-trigger", project_dir)
        assert "proj-fresh" in dm_module._instances


class TestRunningPlanNeverEvicted:
    """A manager with a live 'running' plan must never be evicted --
    tearing it down would orphan its asyncio Tasks."""

    def test_manager_with_running_plan_survives_ttl_pressure(self, project_dir):
        mgr = get_delegate_manager("proj-running", project_dir)
        mgr._plans["plan1"] = TaskPlan(
            name="Test", delegate_specs=[], created_at=time.time(), status="running"
        )
        dm_module._instance_last_used["proj-running"] = (
            time.time() - dm_module._DELEGATE_MANAGER_IDLE_TTL_SECONDS - 1
        )

        get_delegate_manager("proj-trigger", project_dir)

        assert "proj-running" in dm_module._instances

    def test_manager_with_running_plan_survives_cap_pressure(self, project_dir):
        mgr = get_delegate_manager("proj-running", project_dir)
        mgr._plans["plan1"] = TaskPlan(
            name="Test", delegate_specs=[], created_at=time.time(), status="running"
        )
        # Never re-touch "proj-running" -- it has the oldest last_used
        # timestamp of everything below, so it would be the first evicted
        # if not for the running-plan guard.
        cap = dm_module._MAX_DELEGATE_MANAGER_INSTANCES
        for i in range(cap + 20):
            get_delegate_manager(f"proj-{i}", project_dir)

        assert "proj-running" in dm_module._instances

    def test_manager_with_only_completed_plans_is_evictable(self, project_dir):
        """Sanity check on the guard's precision: a manager with no
        *running* plan (e.g. all completed) must still be evictable."""
        mgr = get_delegate_manager("proj-done", project_dir)
        mgr._plans["plan1"] = TaskPlan(
            name="Test", delegate_specs=[], created_at=time.time(), status="completed"
        )
        dm_module._instance_last_used["proj-done"] = (
            time.time() - dm_module._DELEGATE_MANAGER_IDLE_TTL_SECONDS - 1
        )

        get_delegate_manager("proj-trigger", project_dir)

        assert "proj-done" not in dm_module._instances


class TestEvictionDoesNotBreakLegitimateReuse:
    """Baseline: normal singleton-reuse semantics are unaffected."""

    def test_same_project_id_returns_same_instance(self, project_dir):
        m1 = get_delegate_manager("proj-x", project_dir)
        m2 = get_delegate_manager("proj-x", project_dir)
        assert m1 is m2

    def test_reset_clears_both_dicts(self, project_dir):
        get_delegate_manager("proj-x", project_dir)
        assert dm_module._instances
        assert dm_module._instance_last_used

        reset_delegate_manager()

        assert dm_module._instances == {}
        assert dm_module._instance_last_used == {}


class TestNegativeControlPreFixBehavior:
    """
    Reproduces the pre-fix get_delegate_manager logic directly to prove
    the cache previously grew without bound (not tautological).
    """

    def test_prefix_logic_never_evicts(self, project_dir):
        instances: dict = {}

        def prefix_get_delegate_manager(project_id, project_dir_):
            if project_id not in instances:
                instances[project_id] = DelegateManager(project_id, project_dir_)
            return instances[project_id]

        for i in range(200):
            prefix_get_delegate_manager(f"attacker-{i}", project_dir)

        # Proves the old behavior: no cap, no eviction -- every distinct
        # project_id grows the cache forever.
        assert len(instances) == 200
