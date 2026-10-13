"""Jobs a Task block starts are killed when the block finishes
(design/job-runner.md, Ownership; decision 2).

Seam: ``execute_task_block``'s ``finally`` -> ``JobManager.kill_owned``
with the block's ``{run_id, block_id}`` owner.  The model is a fake that
emits one text chunk; the job is started directly on the manager with the
owner the job_start tool would derive from the same run/block.  A job
detached to the run (no block_id in its owner) survives the block and is
killed by the run-level ``kill_owned({"run_id"})``.
"""
import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block

os.environ.setdefault("ZIYA_TESTING", "1")
pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


class _FakeExecutor:
    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from app.context import set_project_root
    set_project_root(str(root))
    import app.jobs.manager as mgr_mod
    import app.jobs.policy as pol_mod
    mgr_mod._MANAGER = None
    pol_mod._POLICY = pol_mod._ShellPolicy()
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _FakeExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "global-model"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield root
    m = mgr_mod.get_job_manager()
    for st in m.list(str(root)):
        if st["state"] == "running":
            m.kill(st["job_id"])


def _block():
    return Block(block_type="task", id="blk-1", name="T", instructions="do it")


@pytest.mark.asyncio
async def test_block_end_kills_its_jobs_but_not_run_detached_ones(proj):
    from app.jobs.manager import get_job_manager
    m = get_job_manager()
    owned = m.start("sleep 100", project_root=str(proj), owner={"run_id": "run-1", "block_id": "blk-1"})
    detached = m.start("sleep 100", project_root=str(proj), owner={"run_id": "run-1"})
    other = m.start("sleep 100", project_root=str(proj), owner={"run_id": "run-2", "block_id": "blk-1"})
    time.sleep(0.5)
    assert all(s["state"] == "running" for s in m.list(str(proj)))  # positive control

    await task_executor.execute_task_block(_block(), project_root=str(proj), run_id="run-1")

    states = {s["job_id"]: s["state"] for s in m.list(str(proj))}
    assert states[owned["job_id"]] != "running", "block end must kill the block's job"
    assert states[detached["job_id"]] == "running", "detach='run' survives the block"
    assert states[other["job_id"]] == "running", "another run's job is untouched"

    # Run end reaches the detached one.
    assert m.kill_owned({"run_id": "run-1"}, "run_end") == [detached["job_id"]]
    assert m.list(str(proj), owner={"run_id": "run-2"})[0]["state"] == "running"
