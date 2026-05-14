"""Tests for scope handling in app.agents.task_executor.

Covers:
  - Skill loading: prompts injected, missing skills warn but don't abort
  - File preloading: contents injected, size caps enforced, path escape
    rejected, non-existent files warn
  - Tool filter: warnings for requested-but-unavailable tools
  - Warning plumbing into Artifact.decisions

execute_task_block is exercised end-to-end by patching the
StreamingToolExecutor so we can inspect the exact messages handed
to the model without making real API calls.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope, Artifact


# ── Helpers ──────────────────────────────────────────────────────────

def _task(instructions: str = "do it", **scope_kwargs) -> Block:
    scope = TaskScope(**scope_kwargs) if scope_kwargs else None
    return Block(
        block_type="task",
        id="task-1",
        name="T",
        instructions=instructions,
        scope=scope,
    )


class _FakeExecutor:
    """Replacement for StreamingToolExecutor that captures messages
    and yields a single text chunk + stream_end."""

    captured_messages = None
    captured_tools = None

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        # Record what the executor was called with so tests can inspect.
        type(self).captured_messages = messages
        type(self).captured_tools = tools
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def fake_executor():
    # Reset capture state between tests.
    _FakeExecutor.captured_messages = None
    _FakeExecutor.captured_tools = None
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _FakeExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield _FakeExecutor


def _system_text(executor_cls) -> str:
    msgs = executor_cls.captured_messages or []
    assert msgs, "executor not called"
    return msgs[0].content  # SystemMessage is first


# ── Structural validation ────────────────────────────────────────────

class TestValidation:
    @pytest.mark.asyncio
    async def test_non_task_block_rejected(self):
        block = Block(block_type="repeat", id="r", name="R",
                      repeat_mode="count", repeat_count=1)
        with pytest.raises(task_executor.TaskExecutorError):
            await task_executor.execute_task_block(block)

    @pytest.mark.asyncio
    async def test_empty_instructions_rejected(self):
        block = Block(block_type="task", id="t", name="T", instructions="  ")
        with pytest.raises(task_executor.TaskExecutorError):
            await task_executor.execute_task_block(block)


# ── Skills ───────────────────────────────────────────────────────────

class TestSkills:
    @pytest.mark.asyncio
    async def test_no_project_id_emits_warning(self, fake_executor):
        block = _task(skills=["some-skill"])
        artifact = await task_executor.execute_task_block(block, project_id=None)
        assert any("no project_id" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_skill_prompt_injected(self, fake_executor, tmp_path, monkeypatch):
        # Build a real skill on disk so SkillStorage resolves it.
        from app.storage.skills import SkillStorage
        from app.services.token_service import TokenService
        monkeypatch.setattr("app.utils.paths.get_project_dir",
                            lambda pid: tmp_path)
        storage = SkillStorage(tmp_path, TokenService())
        # Insert a custom skill directly.
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_data = {
            "id": "my-skill", "name": "My Skill",
            "description": "d", "prompt": "ALWAYS DO X",
            "color": "#000", "tokenCount": 0,
            "isBuiltIn": False, "createdAt": 0, "lastUsedAt": 0,
        }
        (skill_dir / "my-skill.json").write_text(json.dumps(skill_data))

        block = _task(skills=["my-skill"])
        artifact = await task_executor.execute_task_block(
            block, project_id="proj-1",
        )
        sys_text = _system_text(fake_executor)
        assert "[Active Skill: My Skill]" in sys_text
        assert "ALWAYS DO X" in sys_text
        # No warnings for a successfully-loaded skill.
        assert not any("my-skill" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_missing_skill_warns(self, fake_executor, tmp_path, monkeypatch):
        monkeypatch.setattr("app.utils.paths.get_project_dir",
                            lambda pid: tmp_path)
        block = _task(skills=["does-not-exist"])
        artifact = await task_executor.execute_task_block(
            block, project_id="proj-1",
        )
        assert any("does-not-exist" in d and "not found" in d
                   for d in artifact.decisions)


# ── File preload ─────────────────────────────────────────────────────

class TestFilePreload:
    @pytest.mark.asyncio
    async def test_no_project_root_warns(self, fake_executor):
        block = _task(files=["a.py"])
        artifact = await task_executor.execute_task_block(block, project_root=None)
        assert any("no project_root" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_existing_file_injected(self, fake_executor, tmp_path):
        (tmp_path / "hello.py").write_text("print('hi')\n")
        block = _task(files=["hello.py"])
        await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "### hello.py" in sys_text
        assert "print('hi')" in sys_text

    @pytest.mark.asyncio
    async def test_missing_file_warns_but_continues(self, fake_executor, tmp_path):
        (tmp_path / "exists.py").write_text("yes\n")
        block = _task(files=["missing.py", "exists.py"])
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        assert any("missing.py" in d for d in artifact.decisions)
        sys_text = _system_text(fake_executor)
        assert "### exists.py" in sys_text

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, fake_executor, tmp_path):
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("SECRET")
        block = _task(files=["../secret.txt"])
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "SECRET" not in sys_text
        assert any("escapes" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_per_file_size_cap(self, fake_executor, tmp_path, monkeypatch):
        # Shrink the cap so the test stays fast.
        monkeypatch.setattr(task_executor, "_MAX_FILE_BYTES", 100)
        monkeypatch.setattr(task_executor, "_MAX_TOTAL_FILE_BYTES", 1_000_000)
        big = "x" * 500
        (tmp_path / "big.txt").write_text(big)
        block = _task(files=["big.txt"])
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        # Truncated to cap; still present.
        assert "x" * 100 in sys_text
        assert "x" * 500 not in sys_text
        assert any("cap" in d and "big.txt" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_total_size_cap_stops_later_files(
        self, fake_executor, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(task_executor, "_MAX_FILE_BYTES", 1_000_000)
        monkeypatch.setattr(task_executor, "_MAX_TOTAL_FILE_BYTES", 200)
        (tmp_path / "a.txt").write_text("a" * 150)
        (tmp_path / "b.txt").write_text("b" * 150)
        block = _task(files=["a.txt", "b.txt"])
        artifact = await task_executor.execute_task_block(
            block, project_root=str(tmp_path),
        )
        sys_text = _system_text(fake_executor)
        assert "a" * 150 in sys_text
        assert "b" * 150 not in sys_text
        assert any("total preload cap" in d and "b.txt" in d
                   for d in artifact.decisions)


# ── Tool filter ──────────────────────────────────────────────────────

class TestToolFilter:
    @pytest.mark.asyncio
    async def test_unknown_tool_warns(self, fake_executor):
        # create_secure_mcp_tools returns [] in the fixture, so any
        # requested tool is "unavailable".
        block = _task(tools=["mcp_nonexistent"])
        artifact = await task_executor.execute_task_block(block)
        assert any("mcp_nonexistent" in d for d in artifact.decisions)

    @pytest.mark.asyncio
    async def test_empty_scope_exposes_all(self, fake_executor):
        fake_tool = MagicMock()
        fake_tool.name = "mcp_anything"
        with patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[fake_tool]):
            block = _task()  # no scope
            await task_executor.execute_task_block(block)
        assert fake_executor.captured_tools == [fake_tool]
