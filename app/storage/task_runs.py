"""
Task run storage — file-per-run under a project directory.

Follows the same pattern as TaskCardStorage.  Runs are ephemeral
but persist enough for the frontend to poll status and read final
artifacts across reloads.
"""

import time
import uuid
import logging
from pathlib import Path
from typing import Optional, List

from .base import BaseStorage
from ..models.task_run import TaskRun, TaskRunCreate, TaskRunBlockState
from ..models.task_card import Artifact

logger = logging.getLogger(__name__)


class TaskRunStorage(BaseStorage[TaskRun]):
    """CRUD for TaskRuns scoped to a project."""

    def __init__(self, project_dir: Path):
        self.runs_dir = project_dir / "task_runs"
        super().__init__(self.runs_dir)

    def _run_file(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def get(self, run_id: str) -> Optional[TaskRun]:
        data = self._read_json(self._run_file(run_id))
        if data:
            return TaskRun(**data)
        return None

    def list(self, card_id: Optional[str] = None) -> List[TaskRun]:
        runs: List[TaskRun] = []
        if self.runs_dir.exists():
            for run_file in self.runs_dir.glob("*.json"):
                data = self._read_json(run_file)
                if data:
                    try:
                        run = TaskRun(**data)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Skipping corrupt task run {run_file}: {e}")
                        continue
                    if card_id and run.card_id != card_id:
                        continue
                    runs.append(run)
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def create(self, data: TaskRunCreate) -> TaskRun:
        run_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        run = TaskRun(
            id=run_id,
            card_id=data.card_id,
            source_conversation_id=data.source_conversation_id,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def update_status(
        self, run_id: str, status: str,
        error: Optional[str] = None,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.status = status  # type: ignore[assignment]
        if status == "running" and run.started_at is None:
            run.started_at = time.time()
        if status in ("done", "failed", "cancelled"):
            run.completed_at = time.time()
        if error:
            run.error = error
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def set_artifact(
        self, run_id: str, artifact: Artifact,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.artifact = artifact
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def set_block_state(
        self, run_id: str, state: TaskRunBlockState,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.block_states[state.block_id] = state
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def update(self, run_id: str, data) -> Optional[TaskRun]:
        """BaseStorage contract.  Task runs don't have a generic update
        path — use update_status / set_artifact / set_block_state for
        semantic mutations.  This method is here only to satisfy the
        abstract base class."""
        raise NotImplementedError(
            "TaskRun does not support generic update; use update_status, "
            "set_artifact, or set_block_state"
        )

    def delete(self, run_id: str) -> bool:
        run_file = self._run_file(run_id)
        if not run_file.exists():
            return False
        run_file.unlink()
        return True
