"""
Tests for swarm scratch directory management.

Covers:
  - Per-plan and per-delegate directory creation
  - Cleanup on plan completion/deletion
  - GC for stale/aged-out task directories
  - Path traversal sanitization
  - Integration with DelegateManager.cleanup_plan
"""

import os
import time
import uuid

import pytest

from app.agents.swarm_scratch import SwarmScratchManager, get_scratch_manager


@pytest.fixture
def scratch_mgr(tmp_path):
    """Create a SwarmScratchManager rooted at a temp directory."""
    return SwarmScratchManager(str(tmp_path))


class TestDirectoryCreation:
    """Test that task and delegate directories are created correctly."""

    def test_get_task_dir_creates_directory(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        task_dir = scratch_mgr.get_task_dir(plan_id)
        assert task_dir.exists()
        assert task_dir.is_dir()
        assert plan_id in str(task_dir)

    def test_get_delegate_dir_creates_nested_directory(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        delegate_id = "D1"
        delegate_dir = scratch_mgr.get_delegate_dir(plan_id, delegate_id)
        assert delegate_dir.exists()
        assert delegate_dir.is_dir()
        assert delegate_id in str(delegate_dir)
        # Parent should be the task dir
        assert delegate_dir.parent == scratch_mgr.get_task_dir(plan_id)

    def test_get_task_dir_no_create(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        task_dir = scratch_mgr.get_task_dir(plan_id, create=False)
        assert not task_dir.exists()

    def test_idempotent_creation(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        dir1 = scratch_mgr.get_task_dir(plan_id)
        dir2 = scratch_mgr.get_task_dir(plan_id)
        assert dir1 == dir2
        assert dir1.exists()


class TestRelativePaths:
    """Test relative path generation for prompt injection."""

    def test_relative_task_path(self, scratch_mgr):
        plan_id = "abc-123"
        rel = scratch_mgr.get_relative_task_path(plan_id)
        assert rel == ".ziya/tasks/abc-123"

    def test_relative_delegate_path(self, scratch_mgr):
        plan_id = "abc-123"
        delegate_id = "D1"
        rel = scratch_mgr.get_relative_delegate_path(plan_id, delegate_id)
        assert rel == ".ziya/tasks/abc-123/D1"


class TestPathSanitization:
    """Test that path traversal attempts are neutralized."""

    def test_plan_id_with_slashes(self, scratch_mgr):
        plan_id = "../../etc/passwd"
        task_dir = scratch_mgr.get_task_dir(plan_id)
        # Should NOT escape the tasks root
        assert scratch_mgr.tasks_root in task_dir.parents or task_dir.parent == scratch_mgr.tasks_root
        assert ".." not in str(task_dir.relative_to(scratch_mgr.project_root))

    def test_delegate_id_with_slashes(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        delegate_id = "../../../tmp/evil"
        delegate_dir = scratch_mgr.get_delegate_dir(plan_id, delegate_id)
        assert scratch_mgr.tasks_root in delegate_dir.parents or delegate_dir.parent.parent == scratch_mgr.tasks_root
        assert ".." not in str(delegate_dir.relative_to(scratch_mgr.project_root))


class TestCleanup:
    """Test cleanup of task and delegate directories."""

    def test_cleanup_task_removes_directory(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        task_dir = scratch_mgr.get_task_dir(plan_id)
        # Create some files inside
        (task_dir / "notes.md").write_text("hello")
        (task_dir / "D1").mkdir()
        (task_dir / "D1" / "scratch.txt").write_text("world")

        assert task_dir.exists()
        result = scratch_mgr.cleanup_task(plan_id)
        assert result is True
        assert not task_dir.exists()

    def test_cleanup_nonexistent_task(self, scratch_mgr):
        result = scratch_mgr.cleanup_task("nonexistent-plan")
        assert result is False

    def test_cleanup_delegate_removes_only_delegate(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        d1_dir = scratch_mgr.get_delegate_dir(plan_id, "D1")
        d2_dir = scratch_mgr.get_delegate_dir(plan_id, "D2")
        (d1_dir / "file.txt").write_text("d1 stuff")
        (d2_dir / "file.txt").write_text("d2 stuff")

        scratch_mgr.cleanup_delegate(plan_id, "D1")
        assert not d1_dir.exists()
        assert d2_dir.exists()  # D2 should survive


class TestGarbageCollection:
    """Test automatic cleanup of stale task directories."""

    def test_gc_removes_old_directories(self, scratch_mgr):
        old_plan = str(uuid.uuid4())
        new_plan = str(uuid.uuid4())

        old_dir = scratch_mgr.get_task_dir(old_plan)
        new_dir = scratch_mgr.get_task_dir(new_plan)

        # Backdate the old directory's mtime
        old_time = time.time() - (72 * 3600)  # 72 hours ago
        os.utime(old_dir, (old_time, old_time))

        cleaned = scratch_mgr.gc_stale_tasks(max_age_hours=48)
        assert old_plan in cleaned
        assert not old_dir.exists()
        assert new_dir.exists()  # Recent dir survives

    def test_gc_leaves_recent_directories(self, scratch_mgr):
        plan_id = str(uuid.uuid4())
        scratch_mgr.get_task_dir(plan_id)

        cleaned = scratch_mgr.gc_stale_tasks(max_age_hours=48)
        assert len(cleaned) == 0

    def test_gc_empty_tasks_root(self, scratch_mgr):
        # tasks_root doesn't even exist yet
        cleaned = scratch_mgr.gc_stale_tasks()
        assert cleaned == []


class TestListTasks:
    """Test listing task directories with ages."""

    def test_list_tasks_returns_plan_ids(self, scratch_mgr):
        p1 = str(uuid.uuid4())
        p2 = str(uuid.uuid4())
        scratch_mgr.get_task_dir(p1)
        scratch_mgr.get_task_dir(p2)

        tasks = scratch_mgr.list_tasks()
        assert p1 in tasks
        assert p2 in tasks
        # Ages should be very small (just created)
        assert all(age < 0.1 for age in tasks.values())

    def test_list_tasks_empty(self, scratch_mgr):
        assert scratch_mgr.list_tasks() == {}


class TestSingleton:
    """Test the module-level get_scratch_manager cache."""

    def test_same_root_returns_same_instance(self, tmp_path):
        from app.agents.swarm_scratch import _instances
        _instances.clear()  # Reset for test isolation

        mgr1 = get_scratch_manager(str(tmp_path))
        mgr2 = get_scratch_manager(str(tmp_path))
        assert mgr1 is mgr2

    def test_different_roots_return_different_instances(self, tmp_path):
        from app.agents.swarm_scratch import _instances
        _instances.clear()

        dir1 = tmp_path / "project1"
        dir2 = tmp_path / "project2"
        dir1.mkdir()
        dir2.mkdir()

        mgr1 = get_scratch_manager(str(dir1))
        mgr2 = get_scratch_manager(str(dir2))
        assert mgr1 is not mgr2
