"""
Swarm Scratch Directory Manager.

Provides hierarchical scratch space under `.ziya/tasks/{plan_id}/` for
delegate file operations.  Handles:
  - Per-plan directory creation and path resolution
  - Cleanup when plans complete or are deleted
  - Periodic GC for orphaned/aged-out task directories

Usage::

    from app.agents.swarm_scratch import get_scratch_manager

    mgr = get_scratch_manager(project_root)
    task_dir = mgr.get_task_dir(plan_id)            # .ziya/tasks/<plan_id>/
    delegate_dir = mgr.get_delegate_dir(plan_id, did)  # .ziya/tasks/<plan_id>/<did>/
    mgr.cleanup_task(plan_id)                         # rm -rf the task dir
    mgr.gc_stale_tasks(max_age_hours=48)              # prune old dirs
"""

import os
import shutil
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TASKS_SUBDIR = ".ziya/tasks"
DEFAULT_MAX_AGE_HOURS = 48


class SwarmScratchManager:
    """Manages per-plan scratch directories under .ziya/tasks/."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.tasks_root = self.project_root / TASKS_SUBDIR

    def get_task_dir(self, plan_id: str, *, create: bool = True) -> Path:
        """Return the scratch directory for a task plan.

        Args:
            plan_id: The plan's UUID.
            create: Create the directory if it doesn't exist.

        Returns:
            Absolute path to .ziya/tasks/{plan_id}/
        """
        # Sanitize plan_id to prevent path traversal
        safe_id = plan_id.replace("/", "_").replace("..", "_")
        task_dir = self.tasks_root / safe_id
        if create:
            task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def get_delegate_dir(
        self, plan_id: str, delegate_id: str, *, create: bool = True
    ) -> Path:
        """Return the scratch directory for a specific delegate.

        Returns:
            Absolute path to .ziya/tasks/{plan_id}/{delegate_id}/
        """
        safe_did = delegate_id.replace("/", "_").replace("..", "_")
        delegate_dir = self.get_task_dir(plan_id, create=create) / safe_did
        if create:
            delegate_dir.mkdir(parents=True, exist_ok=True)
        return delegate_dir

    def get_relative_task_path(self, plan_id: str) -> str:
        """Return the project-relative path for a task's scratch dir.

        Suitable for passing to file_write tool instructions.
        """
        safe_id = plan_id.replace("/", "_").replace("..", "_")
        return f".ziya/tasks/{safe_id}"

    def get_relative_delegate_path(self, plan_id: str, delegate_id: str) -> str:
        """Return the project-relative path for a delegate's scratch dir."""
        safe_id = plan_id.replace("/", "_").replace("..", "_")
        safe_did = delegate_id.replace("/", "_").replace("..", "_")
        return f".ziya/tasks/{safe_id}/{safe_did}"

    def cleanup_task(self, plan_id: str) -> bool:
        """Remove the entire scratch directory for a completed/deleted plan.

        Returns True if a directory was actually removed.
        """
        task_dir = self.get_task_dir(plan_id, create=False)
        if task_dir.exists() and task_dir.is_dir():
            try:
                shutil.rmtree(task_dir)
                logger.info(
                    f"🗑️ SCRATCH: Cleaned up task directory: {task_dir.relative_to(self.project_root)}"
                )
                return True
            except Exception as exc:
                logger.warning(f"🗑️ SCRATCH: Failed to clean up {task_dir}: {exc}")
        return False

    def cleanup_delegate(self, plan_id: str, delegate_id: str) -> bool:
        """Remove scratch directory for a single delegate."""
        delegate_dir = self.get_delegate_dir(plan_id, delegate_id, create=False)
        if delegate_dir.exists() and delegate_dir.is_dir():
            try:
                shutil.rmtree(delegate_dir)
                logger.info(
                    f"🗑️ SCRATCH: Cleaned up delegate directory: "
                    f"{delegate_dir.relative_to(self.project_root)}"
                )
                return True
            except Exception as exc:
                logger.warning(f"🗑️ SCRATCH: Failed to clean up {delegate_dir}: {exc}")
        return False

    def gc_stale_tasks(
        self, max_age_hours: float = DEFAULT_MAX_AGE_HOURS
    ) -> List[str]:
        """Remove task directories older than max_age_hours.

        Age is determined by the directory's mtime (updated whenever
        any file inside is written).

        Returns list of plan_ids that were cleaned up.
        """
        if not self.tasks_root.exists():
            return []

        cutoff = time.time() - (max_age_hours * 3600)
        cleaned: List[str] = []

        for entry in self.tasks_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(entry)
                    cleaned.append(entry.name)
                    logger.info(
                        f"🗑️ SCRATCH GC: Removed stale task dir "
                        f"{entry.name} (age: {(time.time() - mtime) / 3600:.1f}h)"
                    )
            except Exception as exc:
                logger.warning(f"🗑️ SCRATCH GC: Error processing {entry}: {exc}")

        if cleaned:
            logger.info(f"🗑️ SCRATCH GC: Cleaned {len(cleaned)} stale task dir(s)")
        return cleaned

    def list_tasks(self) -> Dict[str, float]:
        """List all task directories with their ages in hours.

        Returns dict of {plan_id: age_hours}.
        """
        if not self.tasks_root.exists():
            return {}

        result: Dict[str, float] = {}
        now = time.time()
        for entry in self.tasks_root.iterdir():
            if entry.is_dir():
                try:
                    mtime = entry.stat().st_mtime
                    result[entry.name] = (now - mtime) / 3600
                except OSError:
                    pass
        return result


# Module-level cache: project_root → SwarmScratchManager
_instances: Dict[str, SwarmScratchManager] = {}


def get_scratch_manager(project_root: str) -> SwarmScratchManager:
    """Get or create a SwarmScratchManager for the given project."""
    key = os.path.abspath(project_root)
    if key not in _instances:
        _instances[key] = SwarmScratchManager(key)
    return _instances[key]
