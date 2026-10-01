"""A run launched without an X-Project-Root header works in its project.

2026-09-27: a resume of the memory-replay card launched with curl (no
header) got project_root=None and its tasks resolved paths against the
server process's cwd, which was a different repository (ZiyaInternal).
They reported the card's own scripts "absent" and did no work.  Every
launch path (resume, continue, task_card_launch, commands, bindings) goes
through _launch_run_for_card and read the root only from the request.

Asserted on the root the real ``_run`` coroutine is scheduled with: the
stubbed create_task receives that coroutine, so its frame holds exactly
what the executor would use.
"""
import json
import os
import time
from unittest.mock import patch

import pytest

from app.models.task_card import Block, TaskCardCreate
from app.storage.task_cards import TaskCardStorage


def _project(tmp_path, path):
    home = tmp_path / ".ziya"
    pid = "proj-root-fallback"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Root", "path": str(path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000), "lastAccessedAt": int(time.time() * 1000)}))
    card = TaskCardStorage(pdir).create(TaskCardCreate(
        name="c", root=Block(block_type="task", id="t", name="t", instructions="x")))
    return home, pid, pdir, card.id


async def _scheduled_root(home, pid, pdir, card_id, request_root=None):
    from app.api import task_cards as tc
    from app.context import set_project_root
    seen = {}

    def _capture(coro):
        seen["root"] = coro.cr_frame.f_locals.get("project_root")
        coro.close()
        return None

    if request_root is not None:
        set_project_root(request_root)
    with patch.dict("os.environ", {"ZIYA_SKIP_CARD_VALIDATION": "1"}), \
         patch("app.api.task_cards.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.get_ziya_home", return_value=home), \
         patch("app.utils.paths.get_ziya_home", return_value=home), \
         patch("app.api.task_cards.asyncio.create_task", new=_capture):
        await tc._launch_run_for_card(project_id=pid, card_id=card_id)
    assert "root" in seen, "run was never scheduled"
    return seen["root"]


@pytest.mark.asyncio
async def test_headerless_launch_uses_the_project_path(tmp_path):
    work = tmp_path / "repo"; work.mkdir()
    home, pid, pdir, cid = _project(tmp_path, work)
    assert await _scheduled_root(home, pid, pdir, cid) == str(work)


@pytest.mark.asyncio
async def test_explicit_request_root_still_wins(tmp_path):
    work = tmp_path / "repo"; work.mkdir()
    other = tmp_path / "other"; other.mkdir()
    home, pid, pdir, cid = _project(tmp_path, work)
    assert await _scheduled_root(home, pid, pdir, cid, request_root=str(other)) == str(other)


@pytest.mark.asyncio
async def test_missing_project_directory_keeps_old_behaviour(tmp_path):
    home, pid, pdir, cid = _project(tmp_path, tmp_path / "gone")
    assert await _scheduled_root(home, pid, pdir, cid) is None
