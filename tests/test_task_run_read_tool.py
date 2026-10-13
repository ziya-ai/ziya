"""Seam test for the task_run_read tool: the registered tool's execute()
reaches app.utils.task_run_read against the current project's storage.

The reader is covered in test_task_run_read.py; this file proves the tool
is wired — registered in the task_cards category, resolving the project
the way the other task-card tools do, and refusing a run/card mismatch —
so a correct reader that nothing calls cannot pass.
"""

import json

import pytest

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import TaskRunCreate


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    from app.context import set_project_root
    from app.models.project import ProjectCreate
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    repo = tmp_path / "repo"
    repo.mkdir()
    p = ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(repo), name="repo"))
    set_project_root(str(repo))
    pdir = get_project_dir(p.id)
    cards = TaskCardStorage(pdir)
    card = cards.create(TaskCardCreate(
        name="Nightly", root=Block(block_type="task", id="t1", instructions="x")))
    other = cards.create(TaskCardCreate(
        name="Other", root=Block(block_type="task", id="o", instructions="y")))
    runs = TaskRunStorage(pdir)
    run = runs.create(TaskRunCreate(card_id=card.id, source_conversation_id=None))
    runs.set_artifact(run.id, Artifact(summary="s", failed=True, signature="empty_summary",
                                       decisions=["empty_summary: no prose"]))
    runs.update_status(run.id, "failed")
    return {"card": card, "other": other, "run": run}


def test_registered_in_task_cards_category():
    from app.mcp.builtin_tools import get_builtin_tools_for_category
    names = {t().name for t in get_builtin_tools_for_category("task_cards")}
    assert "task_run_read" in names


async def test_read_by_id_and_latest_and_listing(env):
    from app.mcp.tools.task_run_read import TaskRunReadTool
    tool = TaskRunReadTool()

    out = await tool.execute(run_id=env["run"].id)
    assert out["run"]["status"] == "failed"
    assert out["run"]["failure_reason"] == "empty_summary: no prose"
    json.dumps(out)  # transport-safe

    latest = await tool.execute(card="nightly", run_id="latest")
    assert latest["run"]["run_id"] == env["run"].id

    listing = await tool.execute(card=env["card"].id)
    assert listing["count"] == 1 and listing["runs"][0]["run_id"] == env["run"].id
    everything = await tool.execute()
    assert everything["count"] == 1


async def test_refusals(env):
    from app.mcp.tools.task_run_read import TaskRunReadTool
    tool = TaskRunReadTool()
    assert (await tool.execute(card="no such"))["error"]
    assert (await tool.execute(run_id="latest"))["error"]
    assert (await tool.execute(run_id="../etc"))["error"]
    assert (await tool.execute(run_id="nope"))["error"]
    mismatch = await tool.execute(run_id=env["run"].id, card=env["other"].id)
    assert mismatch["error"] and "belongs to card" in mismatch["message"]
    empty = await tool.execute(card=env["other"].id)
    assert empty["runs"] == []
